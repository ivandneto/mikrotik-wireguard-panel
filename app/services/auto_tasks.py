"""Tarefas periódicas por roteador: backup automático da configuração (só guarda quando muda)
e verificação de atualização do RouterOS. Rodam em segundo plano, a partir do coletor."""
import asyncio
import difflib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import config
from ..db import Router, SessionLocal
from ..i18n import tr
from ..mikrotik import MikroTikError, client_for
from . import router_tools
from .alerts import notify

logger = logging.getLogger("wgpanel.tasks")

BACKUP_DIR = Path(config.DATA_DIR) / "router-backups"
STATE_FILE = BACKUP_DIR / "state.json"
NAME_RE = re.compile(r"^\d{8}-\d{6}\.rsc$")
INTERVALS = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}
UPDATE_EVERY = timedelta(hours=24)
_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1), "utf-8")


def router_state(router_id: int) -> dict:
    return load_state().get(str(router_id), {})


def _update_state(router_id: int, **values):
    state = load_state()
    state.setdefault(str(router_id), {}).update(values)
    _save_state(state)


def _due(last: str | None, every: timedelta) -> bool:
    if not last:
        return True
    try:
        return _now() - datetime.fromisoformat(last) >= every
    except ValueError:
        return True


# ------------------------------------------------------------------ backups guardados no painel

def backup_dir(router_id: int) -> Path:
    return BACKUP_DIR / str(router_id)


def list_backups(router_id: int) -> list[dict]:
    d = backup_dir(router_id)
    if not d.exists():
        return []
    files = sorted((f for f in d.iterdir() if NAME_RE.match(f.name)), reverse=True)
    return [{"name": f.name, "size": f.stat().st_size,
             "ts": datetime.strptime(f.stem, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc).isoformat()} for f in files]


def backup_path(router_id: int, name: str) -> Path | None:
    if not NAME_RE.match(name):
        return None
    p = backup_dir(router_id) / name
    return p if p.exists() else None


def diff_with_previous(router_id: int, name: str) -> str:
    names = [b["name"] for b in list_backups(router_id)]
    if name not in names:
        return ""
    i = names.index(name)
    new = backup_dir(router_id) / name
    if i + 1 >= len(names):
        return tr("(primeiro backup: não há versão anterior para comparar)")
    old = backup_dir(router_id) / names[i + 1]
    a = router_tools.normalize_export(old.read_text("utf-8")).splitlines()
    b = router_tools.normalize_export(new.read_text("utf-8")).splitlines()
    return "\n".join(difflib.unified_diff(a, b, names[i + 1], name, lineterm="", n=2))


async def run_backup(router: Router, keep: int, force_check: bool = False) -> dict:
    """Exporta a configuração; salva só se mudou desde o último backup. Retorna {saved, name, message}."""
    text = await router_tools.export_config(client_for(router))
    d = backup_dir(router.id)
    d.mkdir(parents=True, exist_ok=True)
    previous = list_backups(router.id)
    if previous:
        old = (d / previous[0]["name"]).read_text("utf-8")
        if router_tools.normalize_export(old) == router_tools.normalize_export(text):
            _update_state(router.id, backup_checked=_now().isoformat(), backup_error=None)
            return {"saved": False, "name": previous[0]["name"], "message": tr("Nada mudou desde o último backup")}
    name = f"{_now():%Y%m%d-%H%M%S}.rsc"
    (d / name).write_text(text, "utf-8", newline="\n")
    for old in list_backups(router.id)[keep:]:
        (d / old["name"]).unlink(missing_ok=True)
    _update_state(router.id, backup_checked=_now().isoformat(), backup_error=None)
    return {"saved": True, "name": name, "first": not previous,
            "message": tr("Backup salvo: {name}", name=name) if previous else tr("Primeiro backup salvo: {name}", name=name)}


def delete_router_backups(router_id: int):
    d = backup_dir(router_id)
    if d.exists():
        for f in d.iterdir():
            f.unlink(missing_ok=True)
        d.rmdir()
    state = load_state()
    if state.pop(str(router_id), None) is not None:
        _save_state(state)


# ------------------------------------------------------------------ rodada periódica

async def run(router_ids: list[int], settings: dict, prefix_names: bool):
    """Chamado pelo coletor a cada coleta; só trabalha quando algo está vencido."""
    if _lock.locked():
        return
    async with _lock:
        for rid in router_ids:
            db = SessionLocal()
            try:
                router = db.get(Router, rid)
            finally:
                db.close()
            if not router or not router.enabled:
                continue
            st = router_state(rid)
            prefix = f"[{router.name}] " if prefix_names else ""
            if settings.get("auto_backup_enabled") and _due(st.get("backup_checked"),
                                                             INTERVALS.get(settings.get("auto_backup_interval"), INTERVALS["daily"])):
                await _auto_backup(router, settings, prefix)
            if settings.get("update_check_enabled", True) and _due(st.get("update_checked"), UPDATE_EVERY):
                await _check_update(router, prefix)


async def _auto_backup(router: Router, settings: dict, prefix: str):
    try:
        res = await run_backup(router, int(settings.get("auto_backup_keep") or 14))
        if res["saved"] and not res.get("first"):
            await notify("config_changed", prefix + tr("Configuração do MikroTik mudou"),
                         tr("Backup automático salvo: {name}", name=res["name"]), "info")
    except (MikroTikError, OSError) as exc:
        msg = router_tools.permission_error() if router_tools.needs_permission(exc) else str(exc)
        # tenta de novo na próxima rodada do intervalo, sem repetir a cada coleta
        _update_state(router.id, backup_checked=_now().isoformat(), backup_error=msg)
        logger.warning("Backup automático de %s falhou: %s", router.name, msg)


async def _check_update(router: Router, prefix: str):
    try:
        info = await router_tools.check_update(client_for(router))
    except MikroTikError as exc:
        _update_state(router.id, update_checked=_now().isoformat(), update={"error": str(exc)})
        return
    previous = router_state(router.id).get("update") or {}
    _update_state(router.id, update_checked=_now().isoformat(), update=info)
    if info.get("available") and previous.get("latest") != info.get("latest"):
        await notify("router_update", prefix + tr("Atualização do RouterOS disponível"),
                     tr("Versão {latest} disponível (instalada: {installed}). Faça um backup antes de atualizar.",
                        latest=info["latest"], installed=info["installed"]), "info")
