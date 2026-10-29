"""Exportação/importação dos dados do painel e backup do próprio MikroTik."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import NatRule, Peer, Router, User, WgInterface, _migrate, audit, iso, utcnow
from ..mikrotik import MikroTikError, mt, use
from ..mikrotik import main as main_client
from ..routerctx import use_router
from ..security import decrypt, encrypt, get_db, require_admin
from ..services import collector, nat_ops
from ..settings_store import get_settings, save_settings
from .wireguard import PEERS_PATH, _create_on_router, _sync_site

router = APIRouter(prefix="/api/backup", tags=["backup"])

BACKUP_VERSION = 3  # 2: várias interfaces e sites · 3: vários roteadores


class ImportIn(BaseModel):
    data: dict
    apply_to_router: bool = True


ROUTER_FIELDS = ("endpoint_remote", "endpoint_local", "nat_match_mode", "nat_in_target", "wan_interface")


@router.get("/export")
def export_backup(include_alert_secrets: bool = True, user: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    settings = get_settings(db)
    if not include_alert_secrets:
        settings["alerts"].update(telegram_token="", discord_webhook="")
    routers = {r.id: r for r in db.query(Router).all()}
    router_name = {rid: ("" if r.is_main else r.name) for rid, r in routers.items()}  # "" = principal
    ifaces = {i.id: i for i in db.query(WgInterface).all()}
    peers = db.query(Peer).all()
    peer_keys = {p.id: p.public_key for p in peers}
    main = next(r for r in routers.values() if r.is_main)
    data = {
        "app": "mikrotik-wg-panel",
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
    }
    audit(db, user.username, "backup exportado")
    filename = f"wgpanel-backup-{datetime.now():%Y%m%d-%H%M}.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _parse_dt(value):
    return datetime.fromisoformat(value.rstrip("Z")) if value else None


@router.post("/import")
async def import_backup(body: ImportIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Restaura um backup. Só recria itens no MikroTik PRINCIPAL; os dos outros roteadores entram só no painel."""
    data = body.data
    if data.get("app") != "mikrotik-wg-panel":
        raise HTTPException(400, "Arquivo não é um backup deste painel")
    report = {"peers_created": 0, "peers_updated": 0, "nat_created": 0, "users_created": 0, "routers_created": 0,
              "errors": []}
    use(main_client)

    settings_data = data.get("settings", {})
    save_settings(db, settings_data)
    settings = get_settings(db)
    _migrate()  # garante o roteador principal (e a interface de backups da versão 1)
    main = db.query(Router).filter(Router.is_main.is_(True)).first()
    # campos do roteador principal: versão 3 em "main_router"; versões antigas guardavam nas configurações
    main_fields = data.get("main_router") or {k: settings_data[k] for k in ROUTER_FIELDS if k in settings_data}
    for key, value in main_fields.items():
        if key in ROUTER_FIELDS and value:
            setattr(main, key, value)

    routers = {"": main}
    for item in data.get("routers", []):
        r = db.query(Router).filter(Router.name == item["name"], Router.is_main.is_(False)).first()
        if r is None:
            r = Router(name=item["name"], url=item["url"], username=item.get("username", "wgpanel"),
                       password_enc=encrypt(item["password"]) if item.get("password") else "",
                       verify_tls=item.get("verify_tls", False), enabled=item.get("enabled", True),
                       **{k: item.get(k, "") for k in ROUTER_FIELDS if item.get(k)})
            db.add(r)
            report["routers_created"] += 1
        routers[item["name"]] = r
    db.commit()

    for item in data.get("interfaces", []):
        r = routers.get(item.get("router", ""), main)
        exists = db.query(WgInterface).filter(WgInterface.router_id == r.id, WgInterface.name == item["name"]).first()
        if not exists:
            db.add(WgInterface(router_id=r.id, listen_port=int(item["listen_port"]),
                               **{k: item.get(k, "") for k in ("name", "description", "address", "public_key",
                                                                 "dns", "lan_networks")}))
    db.commit()
    ifaces = {(i.router_id, i.name): i for i in db.query(WgInterface).all()}

    router_peers = {}
    if body.apply_to_router:
        try:
            router_peers = {r.get("public-key"): r for r in await mt.get(PEERS_PATH)}
        except MikroTikError as exc:
            report["errors"].append(f"MikroTik principal: {exc}")

    for item in data.get("peers", []):
        r = routers.get(item.get("router", ""), main)
        iface = ifaces.get((r.id, item.get("interface"))) or next(
            (i for (rid, _), i in ifaces.items() if rid == r.id), None)
        peer = db.query(Peer).filter(Peer.public_key == item["public_key"]).first()
        if peer:
            report["peers_updated"] += 1
        else:
            peer = Peer(public_key=item["public_key"], ip=item["ip"])
            db.add(peer)
            report["peers_created"] += 1
        peer.name, peer.description, peer.owner = item["name"], item.get("description", ""), item.get("owner", "")
        peer.private_key_enc = encrypt(item["private_key"]) if item.get("private_key") else ""
        peer.preshared_key_enc = encrypt(item["preshared_key"]) if item.get("preshared_key") else ""
        peer.tunnel_mode, peer.enabled = item.get("tunnel_mode", "split"), item.get("enabled", True)
        peer.kind, peer.remote_networks = item.get("kind", "device"), item.get("remote_networks", "")
        peer.site_access = item.get("site_access", "one_way")
        peer.interface_id = iface.id if iface else None
        peer.created_by = item.get("created_by", user.username)
        db.flush()
        if body.apply_to_router and iface and r.is_main and router_peers is not None:
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

    by_key = {p.public_key: p.id for p in db.query(Peer).all()}
    for item in data.get("routers", []):
        if item.get("site_peer_public_key") in by_key:
            routers[item["name"]].site_peer_id = by_key[item["site_peer_public_key"]]
    db.commit()

    existing_rules = {(r.router_id, r.protocol, r.external_port, r.internal_ip) for r in db.query(NatRule).all()}
    for item in data.get("nat_rules", []):
        r = routers.get(item.get("router", ""), main)
        if (r.id, item["protocol"], item["external_port"], item["internal_ip"]) in existing_rules:
            continue
        rule = NatRule(
            router_id=r.id, name=item["name"], template=item.get("template", ""), protocol=item["protocol"],
            external_port=item["external_port"], internal_ip=item["internal_ip"],
            internal_port=item.get("internal_port", ""), whitelist=item.get("whitelist", ""),
            health_check=item.get("health_check", True), enabled=item.get("enabled", True),
            expires_at=_parse_dt(item.get("expires_at")), created_by=item.get("created_by", user.username),
        )
        db.add(rule)
        db.flush()
        if body.apply_to_router and r.is_main:
            try:
                rule.mikrotik_ids = await nat_ops.apply_rule(rule, main)
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
async def mikrotik_backup(user: User = Depends(require_admin), db: Session = Depends(get_db),
                         rt: Router = Depends(use_router)):
    """Salva um backup (.backup) e uma exportação (.rsc) dentro do próprio MikroTik (menu Files)."""
    name = f"wgpanel-{datetime.now():%Y%m%d-%H%M}"
    await mt.command("/system/backup/save", {"name": name, "dont-encrypt": "yes"})
    files = [f"{name}.backup"]
    try:
        await mt.command("/export", {"file": name})
        files.append(f"{name}.rsc")
    except MikroTikError:
        pass  # algumas versões não expõem /export via REST; o .backup já foi salvo
    audit(db, user.username, "backup do MikroTik criado", f"{rt.name}: " + ", ".join(files))
    return {"ok": True, "files": files}
