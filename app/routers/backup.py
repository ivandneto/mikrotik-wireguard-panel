"""Exportação/importação dos dados do painel e backup do próprio MikroTik."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import NatRule, Peer, Router, User, WgInterface, WolDevice, _migrate, audit
from ..i18n import tr
from ..mikrotik import MikroTikError, mt, use
from ..mikrotik import main as main_client
from ..routerctx import use_router
from ..security import encrypt, get_db, require_admin
from ..services import collector, nat_ops, panel_backup, panel_export
from ..settings_store import get_settings, save_settings
from .wireguard import PEERS_PATH, _create_on_router, _sync_site

router = APIRouter(prefix="/api/backup", tags=["backup"])


class ImportIn(BaseModel):
    data: dict
    apply_to_router: bool = True
    password: str | None = None  # for backups protected with the backup password


ROUTER_FIELDS = panel_export.ROUTER_FIELDS


@router.get("/export")
def export_backup(include_alert_secrets: bool = True, user: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    data = panel_export.build_export(db, include_alert_secrets)
    audit(db, user.username, "backup exportado")
    filename = f"wgpanel-backup-{datetime.now():%Y%m%d-%H%M}.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ------------------------------------------------------------------ automatic panel backup

class AutoBackupIn(BaseModel):
    enabled: bool = True
    keep: int = Field(14, ge=1, le=365)
    send_discord: bool = False
    send_telegram: bool = False
    send_mikrotik: bool = True
    discord_webhook: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    password: str | None = None  # None = keep the current one · "" = remove · text = new password


class UnlockIn(BaseModel):
    data: dict
    password: str


def _auto_status(db: Session) -> dict:
    s = get_settings(db)
    state = panel_backup.load_state()
    return {
        "settings": {"enabled": s["panel_backup_enabled"], "keep": s["panel_backup_keep"],
                     "send_discord": s["panel_backup_send_discord"], "send_telegram": s["panel_backup_send_telegram"],
                     "send_mikrotik": s["panel_backup_send_mikrotik"],
                     "discord_webhook": s["panel_backup_discord_webhook"], "telegram_token": s["panel_backup_telegram_token"],
                     "telegram_chat_id": s["panel_backup_telegram_chat_id"],
                     "password_set": bool(panel_backup.password(s))},
        "channels": {c: bool(v) for c, v in panel_backup.targets(s).items()},
        "backups": panel_backup.list_backups(),
        "state": {k: state.get(k) for k in ("checked", "last_saved", "sent_to", "sent_at", "error", "encrypted")},
    }


@router.get("/auto")
def auto_status(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _auto_status(db)


@router.put("/auto")
def auto_settings(body: AutoBackupIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    webhook = body.discord_webhook.strip()
    if webhook and not webhook.startswith("https://"):
        raise HTTPException(400, tr("O webhook do Discord deve começar com https://"))
    values = {"panel_backup_enabled": body.enabled, "panel_backup_keep": body.keep,
              "panel_backup_send_discord": body.send_discord, "panel_backup_send_telegram": body.send_telegram,
              "panel_backup_send_mikrotik": body.send_mikrotik, "panel_backup_discord_webhook": webhook,
              "panel_backup_telegram_token": body.telegram_token.strip(),
              "panel_backup_telegram_chat_id": body.telegram_chat_id.strip()}
    if body.password is not None:
        if body.password and len(body.password) < 8:
            raise HTTPException(400, tr("A senha do backup precisa ter pelo menos 8 caracteres"))
        values["panel_backup_password_enc"] = encrypt(body.password) if body.password else ""
    has_password = bool(body.password) if body.password is not None else bool(panel_backup.password(get_settings(db)))
    if (body.send_discord or body.send_telegram) and not has_password:
        raise HTTPException(400, tr("Para enviar o backup ao Discord/Telegram, defina antes uma senha de backup: "
                                    "o arquivo tem as chaves da VPN e não pode ir sem proteção."))
    if body.send_discord and not webhook:
        raise HTTPException(400, tr("Para enviar ao Discord, preencha o webhook do canal de backup"))
    if body.send_telegram and not (body.telegram_token.strip() and body.telegram_chat_id.strip()):
        raise HTTPException(400, tr("Para enviar ao Telegram, preencha o token do bot e o chat do canal de backup"))
    save_settings(db, values)
    panel_backup.clear_error()  # an old sending error no longer applies once the settings changed
    audit(db, user.username, "backup automático do painel configurado",
          "senha alterada" if body.password is not None else "")
    return _auto_status(db)


@router.post("/auto/run")
async def auto_run(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    result = await panel_backup.run(force=True)
    audit(db, user.username, "backup do painel feito agora", ", ".join(result.get("sent_to") or []))
    return {**result, **_auto_status(db)}


@router.post("/unlock")
def unlock(body: UnlockIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Opens a protected backup (panel or MikroTik) and returns the readable file, e.g. one taken from Discord."""
    if not panel_export.is_protected(body.data):
        raise HTTPException(400, tr("Este arquivo não é um backup protegido"))
    try:
        data = panel_export.unprotect(body.data, body.password)
    except ValueError:
        raise HTTPException(400, tr("Senha do backup incorreta (ou arquivo danificado)."))
    audit(db, user.username, "backup protegido aberto", data.get("router") or data.get("kind") or "painel")
    if data.get("kind") == "mikrotik-export":
        name = data.get("name") or "mikrotik.rsc"
        return PlainTextResponse(data.get("text", ""), headers={"Content-Disposition": f'attachment; filename="{name}"'})
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="wgpanel-backup.json"'})


@router.get("/auto/{name}")
def auto_download(name: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    path = panel_backup.backup_path(name)
    if not path:
        raise HTTPException(404, tr("Backup não encontrado"))
    audit(db, user.username, "backup do painel baixado", name)
    return FileResponse(path, media_type="application/json", filename=name)


def _parse_dt(value):
    return datetime.fromisoformat(value.rstrip("Z")) if value else None


@router.post("/import")
async def import_backup(body: ImportIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Restaura um backup. Só recria itens no MikroTik PRINCIPAL; os dos outros roteadores entram só no painel."""
    data = body.data
    if data.get("app") != "mikrotik-wg-panel":
        raise HTTPException(400, tr("Arquivo não é um backup deste painel"))
    if panel_export.is_protected(data):
        if not body.password:
            raise HTTPException(400, tr("Este backup é protegido por senha. Digite a senha do backup."))
        try:
            data = panel_export.unprotect(data, body.password)
        except ValueError:
            raise HTTPException(400, tr("Senha do backup incorreta (ou arquivo danificado)."))
    if data.get("kind") == "mikrotik-export":
        raise HTTPException(400, tr("Este arquivo é um backup da configuração de um MikroTik, não do painel. "
                                    "Abra com 🔓 Abrir backup protegido para ler o .rsc."))
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

    for item in data.get("wol_devices", []):
        r = routers.get(item.get("router", ""), main)
        if not db.query(WolDevice).filter(WolDevice.router_id == r.id, WolDevice.mac == item["mac"]).first():
            db.add(WolDevice(router_id=r.id, name=item["name"], mac=item["mac"], interface=item.get("interface", ""),
                             ip=item.get("ip", ""), created_by=user.username))
    db.commit()

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
