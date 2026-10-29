"""Exportação/importação dos dados do painel e backup do próprio MikroTik."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import NatRule, Peer, User, WgInterface, _migrate, audit, iso, utcnow
from ..mikrotik import MikroTikError, mt
from ..security import decrypt, encrypt, get_db, require_admin
from ..services import collector, nat_ops
from ..settings_store import get_settings, save_settings
from .wireguard import PEERS_PATH, _create_on_router, _sync_site

router = APIRouter(prefix="/api/backup", tags=["backup"])

BACKUP_VERSION = 2  # 2: várias interfaces e peers do tipo site


class ImportIn(BaseModel):
    data: dict
    apply_to_router: bool = True


@router.get("/export")
def export_backup(include_alert_secrets: bool = True, user: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    settings = get_settings(db)
    iface_names = {i.id: i.name for i in db.query(WgInterface).all()}
    if not include_alert_secrets:
        settings["alerts"].update(telegram_token="", discord_webhook="")
    data = {
        "app": "mikrotik-wg-panel",
        "version": BACKUP_VERSION,
        "exported_at": iso(utcnow()),
        "settings": settings,
        "interfaces": [{
            "name": i.name, "description": i.description, "listen_port": i.listen_port, "address": i.address,
            "public_key": i.public_key, "dns": i.dns, "lan_networks": i.lan_networks,
        } for i in db.query(WgInterface).all()],
        "peers": [{
            "name": p.name, "description": p.description, "owner": p.owner, "ip": p.ip,
            "public_key": p.public_key,
            "private_key": decrypt(p.private_key_enc) if p.private_key_enc else "",
            "preshared_key": decrypt(p.preshared_key_enc) if p.preshared_key_enc else "",
            "tunnel_mode": p.tunnel_mode, "kind": p.kind, "remote_networks": p.remote_networks,
            "site_access": p.site_access, "interface": iface_names.get(p.interface_id, ""),
            "enabled": p.enabled, "created_by": p.created_by, "created_at": iso(p.created_at),
        } for p in db.query(Peer).all()],
        "nat_rules": [{
            "name": r.name, "template": r.template, "protocol": r.protocol, "external_port": r.external_port,
            "internal_ip": r.internal_ip, "internal_port": r.internal_port, "whitelist": r.whitelist,
            "health_check": r.health_check, "enabled": r.enabled, "expires_at": iso(r.expires_at),
            "created_by": r.created_by,
        } for r in db.query(NatRule).all()],
        "users": [{"username": u.username, "password_hash": u.password_hash, "role": u.role}
                  for u in db.query(User).all()],
    }
    audit(db, user.username, "backup exportado")
    filename = f"wgpanel-backup-{datetime.now():%Y%m%d-%H%M}.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _parse_dt(value):
    return datetime.fromisoformat(value.rstrip("Z")) if value else None


@router.post("/import")
async def import_backup(body: ImportIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    data = body.data
    if data.get("app") != "mikrotik-wg-panel":
        raise HTTPException(400, "Arquivo não é um backup deste painel")
    report = {"peers_created": 0, "peers_updated": 0, "nat_created": 0, "users_created": 0, "errors": []}

    save_settings(db, data.get("settings", {}))
    settings = get_settings(db)

    for item in data.get("interfaces", []):
        if not db.query(WgInterface).filter(WgInterface.name == item["name"]).first():
            db.add(WgInterface(**{k: item.get(k, "") for k in ("name", "description", "address", "public_key",
                                                                 "dns", "lan_networks")},
                               listen_port=int(item["listen_port"])))
    db.commit()
    _migrate()  # backup antigo (versão 1): cria a interface a partir das configurações
    ifaces = {i.name: i for i in db.query(WgInterface).all()}
    default_iface = next(iter(ifaces.values()), None)

    router_peers = {}
    if body.apply_to_router:
        router_peers = {r.get("public-key"): r for r in await mt.get(PEERS_PATH)}

    for item in data.get("peers", []):
        peer = db.query(Peer).filter(Peer.public_key == item["public_key"]).first()
        if peer:
            report["peers_updated"] += 1
        else:
            if db.query(Peer).filter(Peer.ip == item["ip"]).first():
                report["errors"].append(f"Cliente {item['name']}: IP {item['ip']} já está em uso, ignorado")
                continue
            peer = Peer(public_key=item["public_key"], ip=item["ip"])
            db.add(peer)
            report["peers_created"] += 1
        peer.name, peer.description, peer.owner = item["name"], item.get("description", ""), item.get("owner", "")
        peer.private_key_enc = encrypt(item["private_key"]) if item.get("private_key") else ""
        peer.preshared_key_enc = encrypt(item["preshared_key"]) if item.get("preshared_key") else ""
        peer.tunnel_mode, peer.enabled = item.get("tunnel_mode", "split"), item.get("enabled", True)
        peer.kind, peer.remote_networks = item.get("kind", "device"), item.get("remote_networks", "")
        peer.site_access = item.get("site_access", "one_way")
        iface = ifaces.get(item.get("interface")) or default_iface
        peer.interface_id = iface.id if iface else None
        peer.created_by = item.get("created_by", user.username)
        db.flush()
        if body.apply_to_router and iface:
            existing = router_peers.get(peer.public_key)
            try:
                if existing:
                    peer.mikrotik_id = existing[".id"]
                else:
                    peer.mikrotik_id = await _create_on_router(peer, iface, settings)
                    if peer.kind == "site":
                        await _sync_site(peer, iface)
            except MikroTikError as exc:
                report["errors"].append(f"Cliente {peer.name}: {exc}")
        db.commit()

    existing_rules = {(r.protocol, r.external_port, r.internal_ip) for r in db.query(NatRule).all()}
    for item in data.get("nat_rules", []):
        key = (item["protocol"], item["external_port"], item["internal_ip"])
        if key in existing_rules:
            continue
        rule = NatRule(
            name=item["name"], template=item.get("template", ""), protocol=item["protocol"],
            external_port=item["external_port"], internal_ip=item["internal_ip"],
            internal_port=item.get("internal_port", ""), whitelist=item.get("whitelist", ""),
            health_check=item.get("health_check", True), enabled=item.get("enabled", True),
            expires_at=_parse_dt(item.get("expires_at")), created_by=item.get("created_by", user.username),
        )
        db.add(rule)
        db.flush()
        if body.apply_to_router:
            try:
                rule.mikrotik_ids = await nat_ops.apply_rule(rule, settings)
            except MikroTikError as exc:
                report["errors"].append(f"NAT {rule.name}: {exc}")
        db.commit()
        report["nat_created"] += 1

    for item in data.get("users", []):
        if not db.query(User).filter(User.username == item["username"]).first():
            db.add(User(username=item["username"], password_hash=item["password_hash"], role=item["role"]))
            report["users_created"] += 1
    db.commit()

    audit(db, user.username, "backup importado", str(report))
    collector.trigger()
    return report


@router.post("/mikrotik")
async def mikrotik_backup(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Salva um backup (.backup) e uma exportação (.rsc) dentro do próprio MikroTik (menu Files)."""
    name = f"wgpanel-{datetime.now():%Y%m%d-%H%M}"
    await mt.command("/system/backup/save", {"name": name, "dont-encrypt": "yes"})
    files = [f"{name}.backup"]
    try:
        await mt.command("/export", {"file": name})
        files.append(f"{name}.rsc")
    except MikroTikError:
        pass  # algumas versões não expõem /export via REST; o .backup já foi salvo
    audit(db, user.username, "backup do MikroTik criado", ", ".join(files))
    return {"ok": True, "files": files}
