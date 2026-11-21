"""Automatic backup of the panel itself: clients with their keys, ports, routers, settings and users.

Every 10 minutes (and on "Run now") the panel builds the backup in memory; when something changed since the last copy it saves
a file in DATA_DIR/panel-backups (keeping the last N). With a backup password set, the copy is encrypted and can
also be sent to Discord and/or Telegram, so it survives the loss of the disk the panel runs on."""
import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import config
from ..db import SessionLocal, log_event
from ..i18n import tr
from ..security import decrypt
from ..settings_store import get_settings
from . import alerts, panel_export

logger = logging.getLogger("wgpanel.panel_backup")

BACKUP_DIR = Path(config.DATA_DIR) / "panel-backups"
STATE_FILE = BACKUP_DIR / "state.json"
NAME_RE = re.compile(r"^wgpanel-backup-\d{8}-\d{6}\.json$")
EVERY = timedelta(minutes=10)  # comparing a fingerprint of the database is cheap; files are only written on change


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(**values):
    state = load_state()
    state.update(values)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1), "utf-8")


def due() -> bool:
    last = load_state().get("checked")
    try:
        return not last or _now() - datetime.fromisoformat(last) >= EVERY
    except ValueError:
        return True


def list_backups() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    out = []
    for f in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if NAME_RE.match(f.name):
            st = f.stat()
            out.append({"name": f.name, "size": st.st_size,
                        "ts": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")})
    return out


def backup_path(name: str) -> Path | None:
    if not NAME_RE.match(name):
        return None
    path = BACKUP_DIR / name
    return path if path.is_file() else None


def _fingerprint(data: dict) -> str:
    """Same content → same fingerprint (the export time doesn't count)."""
    body = {k: v for k, v in data.items() if k != "exported_at"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def password(settings: dict) -> str:
    enc = settings.get("panel_backup_password_enc") or ""
    try:
        return decrypt(enc) if enc else ""
    except Exception:  # SECRET_KEY changed: the stored password can't be read anymore
        return ""


# ------------------------------------------------------------------ backup channels

def targets(settings: dict) -> dict:
    """The backup channels, configured on the Backup screen and separate from the Alerts channels.
    {"discord": webhook | None, "telegram": (token, chat_id) | None}"""
    webhook = (settings.get("panel_backup_discord_webhook") or "").strip()
    token = (settings.get("panel_backup_telegram_token") or "").strip()
    chat = (settings.get("panel_backup_telegram_chat_id") or "").strip()
    return {"discord": webhook or None, "telegram": (token, chat) if token and chat else None}


async def send_file(settings: dict, name: str, content: bytes, message: str) -> tuple[list[str], list[str]]:
    """Sends a (protected) backup file to the channels turned on in the backup settings."""
    tg = targets(settings)
    jobs, errors = [], []
    if settings.get("panel_backup_send_discord"):
        if tg["discord"]:
            jobs.append(("Discord", alerts.send_discord_file(tg["discord"], name, content, message)))
        else:
            errors.append(tr("Discord: preencha o webhook do canal de backup"))
    if settings.get("panel_backup_send_telegram"):
        if tg["telegram"]:
            jobs.append(("Telegram", alerts.send_telegram_file(*tg["telegram"], name, content, message)))
        else:
            errors.append(tr("Telegram: preencha o token do bot e o chat do canal de backup"))
    sent = []
    for channel, job in jobs:
        try:
            await job
            sent.append(channel)
        except Exception as exc:  # network/API errors: keep the local copy, report the failure
            errors.append(f"{channel}: {exc}")
    return sent, errors


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()).strip("-") or "mikrotik"


async def send_mikrotik_copy(router_name: str, name: str, text: str):
    """Sends a new MikroTik configuration backup (.rsc) to the backup channel, protected by the backup password.
    Independent from the panel backup being on: it only needs the backup password and a backup channel turned on."""
    db = SessionLocal()
    try:
        settings = get_settings(db)
    finally:
        db.close()
    pw = password(settings)
    if not (settings.get("panel_backup_send_mikrotik", True) and pw
            and (settings.get("panel_backup_send_discord") or settings.get("panel_backup_send_telegram"))):
        return
    payload = panel_export.protect({"app": panel_export.APP_ID, "kind": "mikrotik-export", "router": router_name,
                                    "name": name, "text": text}, pw)
    payload["kind"] = "mikrotik-export"
    filename = f"{_slug(router_name)}-{name.removesuffix('.rsc')}.json"
    message = tr("🗄️ Configuração do MikroTik {router} mudou ({date}). Backup protegido com a senha de backup: "
                 "para ler, Backup → 🔓 Abrir backup protegido no painel.", router=router_name,
                 date=f"{datetime.now():%d/%m/%Y %H:%M}")
    sent, errors = await send_file(settings, filename, json.dumps(payload).encode(), message)
    if errors:
        db = SessionLocal()
        try:
            log_event(db, "warn", "panel_backup", tr("Backup do MikroTik {router} salvo, mas o envio falhou: {error}",
                                                     router=router_name, error="; ".join(errors)))
        finally:
            db.close()


async def run(force: bool = False) -> dict:
    """Builds the backup; saves (and sends) only when something changed, unless force=True."""
    db = SessionLocal()
    try:
        settings = get_settings(db)
        data = panel_export.build_export(db, include_alert_secrets=True)
    finally:
        db.close()
    fp = _fingerprint(data)
    state = load_state()
    now = _now().isoformat(timespec="seconds")
    if not force and fp == state.get("fingerprint"):
        _save_state(checked=now)
        return {"saved": False, "name": state.get("last_name")}

    pw = password(settings)
    payload = panel_export.protect(data, pw) if pw else data
    content = json.dumps(payload, ensure_ascii=False, indent=None if pw else 1).encode()
    name = f"wgpanel-backup-{_now():%Y%m%d-%H%M%S}.json"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    (BACKUP_DIR / name).write_bytes(content)
    keep = max(1, int(settings.get("panel_backup_keep") or 14))
    for old in list_backups()[keep:]:
        (BACKUP_DIR / old["name"]).unlink(missing_ok=True)

    sent, errors = [], []
    if pw:  # never send an unprotected backup (it has the VPN keys) to a chat
        message = tr("🛡️ Backup do WG Panel ({date}). Protegido com a senha de backup: para restaurar, "
                     "Backup → Restaurar no painel e digite a senha.", date=f"{datetime.now():%d/%m/%Y %H:%M}")
        sent, errors = await send_file(settings, name, content, message)
    error = "; ".join(errors) or None
    _save_state(checked=now, fingerprint=fp, last_name=name, last_saved=now, encrypted=bool(pw),
                sent_to=sent, sent_at=now if sent else state.get("sent_at"), error=error)
    if error:
        db = SessionLocal()
        try:
            log_event(db, "warn", "panel_backup", tr("Backup do painel salvo, mas o envio falhou: {error}", error=error))
        finally:
            db.close()
        logger.warning("Panel backup %s saved, sending failed: %s", name, error)
    return {"saved": True, "name": name, "sent_to": sent, "error": error, "encrypted": bool(pw)}
