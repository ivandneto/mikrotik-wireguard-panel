"""Cliente da API REST do RouterOS v7 (https://help.mikrotik.com/docs/display/ROS/REST+API).

Mapeamento REST -> comandos do RouterOS:
  GET    /rest/<menu>          -> print (aceita filtros ?campo=valor)
  PUT    /rest/<menu>          -> add
  PATCH  /rest/<menu>/<id>     -> set
  DELETE /rest/<menu>/<id>     -> remove
  POST   /rest/<menu>/<cmd>    -> qualquer comando (ex.: add com place-before)
Todos os valores trafegam como texto.

Vários roteadores: cada um tem seu cliente (client_for). O objeto `mt` usado pelo resto do código
aponta para o roteador "da vez" — o escolhido no seletor do painel (por requisição) ou o que o
coletor está lendo — guardado num ContextVar.
"""
import asyncio
import codecs
import contextvars
import json

import httpx

from .config import config
from .i18n import tr


def _cp1252_fallback(exc: UnicodeDecodeError):
    """Bytes que não são UTF-8 válido são lidos como Windows-1252."""
    chunk = exc.object[exc.start:exc.end]
    return chunk.decode("cp1252", errors="replace"), exc.end


codecs.register_error("cp1252-fallback", _cp1252_fallback)


class MikroTikError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class MikroTik:
    def __init__(self, url: str, user: str, password: str, verify_tls: bool = False, label: str = "MikroTik",
                 timeout: float | None = None):
        self.url = url.rstrip("/")
        self.user = user
        self.password = password
        self.verify_tls = verify_tls
        self.label = label
        self.timeout = timeout or config.MIKROTIK_TIMEOUT
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=f"{self.url}/rest",
                auth=(self.user, self.password),
                verify=self.verify_tls,
                timeout=self.timeout,
            )
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(self, method: str, path: str, **kwargs):
        try:
            resp = await self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise MikroTikError(
                tr("Não foi possível conectar ao MikroTik {name} em {url} ({error})",
                   name=self.label, url=self.url, error=exc.__class__.__name__)
            ) from exc
        if resp.status_code == 401:
            raise MikroTikError(tr("Usuário ou senha do MikroTik inválidos (ou usuário sem permissão rest-api)"), 401)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                message = body.get("detail") or body.get("message") or resp.text
            except ValueError:
                message = resp.text or resp.reason_phrase
            raise MikroTikError(message, resp.status_code)
        if not resp.content:
            return None
        # o RouterOS mistura UTF-8 e Windows-1252 (texto digitado no Winbox) na mesma resposta
        return json.loads(resp.content.decode("utf-8", errors="cp1252-fallback"))

    async def get(self, path: str, **filters):
        return await self.request("GET", path, params=filters or None)

    async def add(self, path: str, data: dict) -> dict:
        return await self.request("PUT", path, json=_stringify(data))

    async def set(self, path: str, item_id: str, data: dict):
        return await self.request("PATCH", f"{path}/{item_id}", json=_stringify(data))

    async def remove(self, path: str, item_id: str, missing_ok: bool = True):
        try:
            return await self.request("DELETE", f"{path}/{item_id}")
        except MikroTikError as exc:
            if missing_ok and exc.status in (400, 404):
                return None
            raise

    async def command(self, path: str, data: dict | None = None):
        return await self.request("POST", path, json=_stringify(data or {}))


def _stringify(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out


main = MikroTik(config.MIKROTIK_URL, config.MIKROTIK_USER, config.MIKROTIK_PASSWORD,
                config.MIKROTIK_VERIFY_TLS, label="Principal")
_current: contextvars.ContextVar["MikroTik | None"] = contextvars.ContextVar("mikrotik", default=None)
_clients: dict[int, tuple[str, MikroTik]] = {}


def client_for(router) -> MikroTik:
    """Cliente da API para um registro de roteador (o principal usa sempre o .env)."""
    if router.is_main:
        return main
    from .security import decrypt  # evita import circular

    signature = f"{router.url}|{router.username}|{router.password_enc}|{router.verify_tls}|{router.name}"
    cached = _clients.get(router.id)
    if cached and cached[0] == signature:
        return cached[1]
    if cached:
        _close_later(cached[1])
    client = MikroTik(router.url, router.username, decrypt(router.password_enc) if router.password_enc else "",
                      router.verify_tls, label=router.name)
    _clients[router.id] = (signature, client)
    return client


def use(client: MikroTik):
    """Define o roteador "da vez" para o restante da requisição/tarefa."""
    _current.set(client)


def _close_later(client: MikroTik):
    """Fecha a conexão antiga sem bloquear; fora do laço async (rotas síncronas), o coletor de lixo resolve."""
    try:
        asyncio.get_running_loop().create_task(client.close())
    except RuntimeError:
        pass


def forget_client(router_id: int):
    cached = _clients.pop(router_id, None)
    if cached:
        _close_later(cached[1])


async def close_all():
    await main.close()
    for _, client in _clients.values():
        await client.close()


class _Current:
    """Encaminha mt.get/mt.add/... para o roteador da vez (ou o principal)."""

    def __getattr__(self, name):
        return getattr(_current.get() or main, name)


mt = _Current()
