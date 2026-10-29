"""Cliente da API REST do RouterOS v7 (https://help.mikrotik.com/docs/display/ROS/REST+API).

Mapeamento REST -> comandos do RouterOS:
  GET    /rest/<menu>          -> print (aceita filtros ?campo=valor)
  PUT    /rest/<menu>          -> add
  PATCH  /rest/<menu>/<id>     -> set
  DELETE /rest/<menu>/<id>     -> remove
  POST   /rest/<menu>/<cmd>    -> qualquer comando (ex.: add com place-before)
Todos os valores trafegam como texto.
"""
import httpx

from .config import config


class MikroTikError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class MikroTik:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=f"{config.MIKROTIK_URL}/rest",
                auth=(config.MIKROTIK_USER, config.MIKROTIK_PASSWORD),
                verify=config.MIKROTIK_VERIFY_TLS,
                timeout=config.MIKROTIK_TIMEOUT,
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
                f"Não foi possível conectar ao MikroTik em {config.MIKROTIK_URL} ({exc.__class__.__name__})"
            ) from exc
        if resp.status_code == 401:
            raise MikroTikError("Usuário ou senha do MikroTik inválidos (ou usuário sem permissão rest-api)", 401)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                message = body.get("detail") or body.get("message") or resp.text
            except ValueError:
                message = resp.text or resp.reason_phrase
            raise MikroTikError(message, resp.status_code)
        if not resp.content:
            return None
        return resp.json()

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


mt = MikroTik()
