"""Ready-to-paste update commands for the panel, for the way it is installed.

For the container inside the MikroTik, the panel reads its own container settings from RouterOS (name, interface,
folders, env/mount lists, DNS, memory) so the commands match this installation. Only those fields are used: the
container's environment (which holds passwords) is never read into the guide."""

from .. import __version__
from ..config import config
from ..i18n import tr
from ..mikrotik import MikroTikError
from ..mikrotik import main as main_client
from . import auto_tasks

REPO_URL = f"https://github.com/{auto_tasks.PANEL_REPO}"
DEFAULTS = {"name": "wgpanel", "interface": "veth-wgpanel", "root-dir": "usb1/wgpanel/root", "envlists": "wgpanel",
            "mountlists": "wgpanel", "dns": "172.20.0.1", "memory-max": "256M", "disk": "usb1"}
# fields copied from the current container into the new one (never env/env-current: they hold passwords)
SAFE_FIELDS = ("name", "interface", "root-dir", "envlists", "envlist", "mountlists", "mounts", "dns", "memory-max",
               "start-on-boot", "logging", "file", "comment")


def _memory(value: str) -> str | None:
    if not value or value == "unlimited":
        return None
    if value.isdigit():
        n = int(value)
        for unit, size in (("G", 1 << 30), ("M", 1 << 20)):
            if n % size == 0:
                return f"{n // size}{unit}"
        return str(n)
    return value


async def _own_container() -> tuple[dict, bool]:
    """This panel's container on the main MikroTik (only the safe fields), and whether it was found."""
    try:
        rows = await main_client.get("/container")
    except MikroTikError:
        return dict(DEFAULTS), False
    ours = [r for r in rows if "app.main:app" in (r.get("default-cmd") or "") or (r.get("tag") or "").endswith("-arm64")
            and "wgpanel" in (r.get("file") or r.get("name") or "")]
    if not ours:
        return dict(DEFAULTS), False
    row = next((r for r in ours if r.get("name") == "wgpanel"), ours[0])
    found = {k: row[k] for k in SAFE_FIELDS if row.get(k) not in (None, "")}
    info = dict(DEFAULTS)
    info.update(found)
    info["root-dir"] = info["root-dir"].lstrip("/")
    previous_file = found.get("file", "").lstrip("/")
    info["disk"] = (previous_file.split("/")[0] if "/" in previous_file else info["root-dir"].split("/")[0]) or "usb1"
    return info, True


def _mikrotik_steps(version: str, c: dict, found: bool) -> list[dict]:
    name, disk = c["name"], c["disk"]
    tar = f"wgpanel-mikrotik-arm64-v{version}.tar"
    env_key = "envlists" if "envlists" in c or "envlist" not in c else "envlist"
    mount_key = "mountlists" if "mountlists" in c or "mounts" not in c else "mounts"
    add = [f"name={name}", f"file={disk}/{tar}", f'comment="WG Panel v{version}"', f"interface={c['interface']}",
           f"root-dir={c['root-dir']}"]
    if c.get(env_key):
        add.append(f"{env_key}={c[env_key]}")
    if c.get(mount_key):
        add.append(f"{mount_key}={c[mount_key]}")
    if c.get("dns"):
        add.append(f"dns={c['dns']}")
    add += ["start-on-boot=yes", "logging=yes"]
    mem = _memory(c.get("memory-max", ""))
    if mem:
        add.append(f"memory-max={mem}")
    return [
        {"title": tr("Antes de começar"),
         "text": tr("Faça um backup do painel (Backup → 🛡️ Backup automático do painel → ▶️ Fazer agora). "
                    "Os dados ficam no pendrive e não são apagados, mas é bom ter uma cópia. Durante a troca o painel "
                    "fica fora do ar por alguns minutos; a VPN continua funcionando.")},
        {"title": tr("1. Baixar o arquivo da versão nova"),
         "text": tr("Baixe {file} no computador:", file=tar),
         "link": f"{REPO_URL}/releases/download/v{version}/{tar}"},
        {"title": tr("2. Parar o painel"),
         "text": tr("No Terminal do Winbox. Depois de parar, rode /container print a cada poucos segundos até o {name} "
                    "aparecer SEM o R na frente. Pode levar alguns segundos.", name=name),
         "commands": [f"/container stop [find name={name}]", "/container print"]},
        {"title": tr("3. Apagar o container antigo"),
         "text": tr("⚠️ Só depois que o R sumir. Se o container ainda estiver rodando, o MikroTik recusa a remoção e "
                    "o passo 5 dá o erro \"root-dir overlap\". Confira com /container print que o {name} sumiu da lista.",
                    name=name),
         "commands": [f"/container remove [find name={name}]", f'/file remove [find name~"{disk}/wgpanel-mikrotik-arm64"]',
                      "/container print"]},
        {"title": tr("4. Enviar o arquivo novo"),
         "text": tr("Arraste {file} para o {disk} na janela Files do Winbox e espere a cópia terminar.", file=tar, disk=disk)},
        {"title": tr("5. Criar e ligar o container"),
         "text": tr("Rode o primeiro comando, espere o /container print mostrar que terminou de extrair, e então o start."),
         "commands": ["/container add " + " ".join(add), "/container print", f"/container start [find name={name}]"]},
        {"title": tr("6. Conferir"),
         "text": tr("Abra o painel e aperte Ctrl+F5: o menu lateral deve mostrar v{version}.", version=version)
                 + ("" if found else " " + tr("⚠️ Não consegui ler a configuração do container no MikroTik: os comandos usam os "
                                             "nomes padrão do README. Confira com /container print antes de rodar."))},
    ]


def _docker_steps(version: str) -> list[dict]:
    archive = f"wgpanel-docker-amd64-v{version}.tar.gz"
    return [
        {"title": tr("Antes de começar"),
         "text": tr("Faça um backup do painel (Backup → 🛡️ Backup automático do painel → ▶️ Fazer agora). "
                    "Os dados ficam na pasta data/ e não são apagados.")},
        {"title": tr("1. Baixar a imagem da versão nova"),
         "text": tr("Baixe {file} para a pasta onde está o docker-compose.yml:", file=archive),
         "link": f"{REPO_URL}/releases/download/v{version}/{archive}"},
        {"title": tr("2. Carregar e reiniciar"),
         "text": tr("Num terminal, dentro dessa pasta:"),
         "commands": [f"docker load -i {archive}", "docker compose up -d"]},
        {"title": tr("Usa o código-fonte (git)?"),
         "text": tr("Em vez dos passos 1 e 2:"),
         "commands": ["git pull", "docker compose up -d --build"]},
        {"title": tr("3. Conferir"),
         "text": tr("Abra o painel e aperte Ctrl+F5: o menu lateral deve mostrar v{version}.", version=version)},
    ]


def _source_steps(version: str) -> list[dict]:
    return [
        {"title": tr("Atualizar a partir do código"),
         "text": tr("Na pasta do projeto, pare o painel e rode:"),
         "commands": ["git pull", "pip install -r requirements.txt"]},
        {"title": tr("Conferir"),
         "text": tr("Ligue o painel de novo e aperte Ctrl+F5: o menu lateral deve mostrar v{version}.", version=version)},
    ]


async def guide() -> dict:
    state = auto_tasks.panel_state()
    latest = state.get("latest") if state.get("available") else None
    version = latest or __version__
    kind = config.INSTALL_KIND if config.INSTALL_KIND in ("mikrotik", "docker") else "source"
    found = None
    if kind == "mikrotik":
        container, found = await _own_container()
        steps = _mikrotik_steps(version, container, found)
    elif kind == "docker":
        steps = _docker_steps(version)
    else:
        steps = _source_steps(version)
    return {"install": kind, "current": __version__, "latest": latest, "version": version,
            "release_url": state.get("url") or f"{REPO_URL}/releases/latest",
            "container_found": found, "steps": steps}

