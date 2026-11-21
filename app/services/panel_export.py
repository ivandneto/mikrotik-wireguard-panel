"""Builds the panel backup (clients with keys, ports, routers, settings and users) and protects it with a password.

Used by the manual export (Backup → Download backup) and by the automatic panel backup."""
import base64
import json
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy.orm import Session

from ..db import NatRule, Peer, Router, User, WgInterface, WolDevice, iso, utcnow
from ..security import decrypt
from ..settings_store import get_settings

APP_ID = "mikrotik-wg-panel"
BACKUP_VERSION = 3  # 2: several interfaces and sites · 3: several routers
ROUTER_FIELDS = ("endpoint_remote", "endpoint_local", "nat_match_mode", "nat_in_target", "wan_interface")
KDF_ITERATIONS = 390_000


def build_export(db: Session, include_alert_secrets: bool = True) -> dict:
    settings = get_settings(db)
    if not include_alert_secrets:
        settings["alerts"].update(telegram_token="", discord_webhook="")
    settings.pop("panel_backup_password_enc", None)  # never put the backup password inside the backup
    routers = {r.id: r for r in db.query(Router).all()}
    router_name = {rid: ("" if r.is_main else r.name) for rid, r in routers.items()}  # "" = main router
    ifaces = {i.id: i for i in db.query(WgInterface).all()}
    peers = db.query(Peer).all()
    peer_keys = {p.id: p.public_key for p in peers}
    main = next(r for r in routers.values() if r.is_main)
    return {
        "app": APP_ID,
        "version": BACKUP_VERSION,
        "exported_at": iso(utcnow()),
        "settings": settings,
        "main_router": {k: getattr(main, k) for k in ROUTER_FIELDS},
        "routers": [{
            "name": r.name, "url": r.url, "username": r.username,
            "password": decrypt(r.password_enc) if r.password_enc else "", "verify_tls": r.verify_tls,
            "enabled": r.enabled, "site_peer_public_key": peer_keys.get(r.site_peer_id, ""),
            **{k: getattr(r, k) for k in ROUTER_FIELDS},
        } for r in routers.values() if not r.is_main],
        "interfaces": [{
            "router": router_name.get(i.router_id, ""), "name": i.name, "description": i.description,
            "listen_port": i.listen_port, "address": i.address, "public_key": i.public_key, "dns": i.dns,
            "lan_networks": i.lan_networks,
        } for i in ifaces.values()],
        "peers": [{
            "name": p.name, "description": p.description, "owner": p.owner, "ip": p.ip,
            "public_key": p.public_key,
            "private_key": decrypt(p.private_key_enc) if p.private_key_enc else "",
            "preshared_key": decrypt(p.preshared_key_enc) if p.preshared_key_enc else "",
            "tunnel_mode": p.tunnel_mode, "kind": p.kind, "remote_networks": p.remote_networks,
            "site_access": p.site_access,
            "router": router_name.get(ifaces[p.interface_id].router_id, "") if p.interface_id in ifaces else "",
            "interface": ifaces[p.interface_id].name if p.interface_id in ifaces else "",
            "enabled": p.enabled, "created_by": p.created_by, "created_at": iso(p.created_at),
        } for p in peers],
        "nat_rules": [{
            "router": router_name.get(r.router_id, ""), "name": r.name, "template": r.template,
            "protocol": r.protocol, "external_port": r.external_port, "internal_ip": r.internal_ip,
            "internal_port": r.internal_port, "whitelist": r.whitelist, "health_check": r.health_check,
            "enabled": r.enabled, "expires_at": iso(r.expires_at), "created_by": r.created_by,
        } for r in db.query(NatRule).all()],
        "users": [{"username": u.username, "password_hash": u.password_hash, "role": u.role}
                  for u in db.query(User).all()],
        "wol_devices": [{"router": router_name.get(d.router_id, ""), "name": d.name, "mac": d.mac,
                         "interface": d.interface, "ip": d.ip} for d in db.query(WolDevice).all()],
    }


# ------------------------------------------------------------------ password protection

def _key(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def protect(data: dict, password: str) -> dict:
    """Wraps a backup so it can only be read with the password (PBKDF2-SHA256 + Fernet/AES)."""
    salt = os.urandom(16)
    token = Fernet(_key(password, salt, KDF_ITERATIONS)).encrypt(json.dumps(data, ensure_ascii=False).encode())
    return {"app": APP_ID, "encrypted": True, "version": data.get("version"), "exported_at": data.get("exported_at"),
            "kdf": "pbkdf2-sha256", "iterations": KDF_ITERATIONS,
            "salt": base64.b64encode(salt).decode(), "data": token.decode()}


def is_protected(wrapper: dict) -> bool:
    return bool(wrapper.get("encrypted")) and "data" in wrapper and "salt" in wrapper


def unprotect(wrapper: dict, password: str) -> dict:
    """Opens a protected backup. Raises ValueError when the password is wrong or the file is damaged."""
    try:
        key = _key(password, base64.b64decode(wrapper["salt"]), int(wrapper.get("iterations") or KDF_ITERATIONS))
        return json.loads(Fernet(key).decrypt(wrapper["data"].encode()))
    except (InvalidToken, KeyError, ValueError, TypeError) as exc:
        raise ValueError("wrong password or damaged file") from exc
