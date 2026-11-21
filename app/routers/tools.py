"""Ferramentas do roteador selecionado: ping, traceroute, checagem de segurança, backups automáticos e
atualização do RouterOS."""
import ipaddress
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import Router, User, WolDevice, audit, iso
from ..i18n import tr
from ..mikrotik import MikroTikError, mt
from ..routerctx import use_router
from ..security import get_db, require_admin
from ..services import auto_tasks, router_tools
from ..settings_store import get_settings

router = APIRouter(prefix="/api/tools", tags=["tools"], dependencies=[Depends(use_router)])


class PingIn(BaseModel):
    address: str = Field(min_length=1, max_length=253)
    count: int = Field(4, ge=1, le=20)


class TraceIn(BaseModel):
    address: str = Field(min_length=1, max_length=253)


class ConsoleIn(BaseModel):
    command: str = Field(min_length=1, max_length=2000)


def _host(address: str) -> str:
    address = address.strip()
    if not router_tools.HOST_RE.match(address):
        raise HTTPException(400, tr("Endereço inválido: use um IP ou um nome (ex.: 8.8.8.8, google.com)"))
    return address


def _mikrotik_error(exc: MikroTikError):
    detail = router_tools.permission_error() if router_tools.needs_permission(exc) else str(exc)
    return HTTPException(502, detail)


@router.post("/ping")
async def ping(body: PingIn, _: User = Depends(require_admin)):
    try:
        return await router_tools.ping(mt, _host(body.address), body.count)
    except MikroTikError as exc:
        raise _mikrotik_error(exc)


@router.post("/traceroute")
async def traceroute(body: TraceIn, _: User = Depends(require_admin)):
    try:
        return {"hops": await router_tools.traceroute(mt, _host(body.address))}
    except MikroTikError as exc:
        raise _mikrotik_error(exc)


@router.post("/console")
async def console(body: ConsoleIn, user: User = Depends(require_admin), db: Session = Depends(get_db),
                  rt: Router = Depends(use_router)):
    """Command console: runs a command on the selected MikroTik and returns its text output."""
    command = body.command.strip()
    if not command:
        raise HTTPException(400, tr("Digite um comando"))
    if router_tools.endless_command(command):
        raise HTTPException(400, tr("Esse comando fica rodando até ser interrompido. Coloque um limite, por exemplo "
                                    "count=4 (ping) ou duration=10s (torch, monitor-traffic)."))
    audit(db, user.username, "comando no console do MikroTik", f"{rt.name}: {command}")  # logged even if it fails
    try:
        return {"output": await router_tools.run_command(mt, command)}
    except MikroTikError as exc:
        # 400 = the MikroTik didn't understand the command (typo): show its own message
        raise HTTPException(exc.status if exc.status in (400, 403, 504) else 502, str(exc))


# ------------------------------------------------------------------ Wake-on-LAN

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


class WolDeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    mac: str
    interface: str = Field("", max_length=64)
    ip: str = ""


def _wol_out(d: WolDevice) -> dict:
    return {"id": d.id, "name": d.name, "mac": d.mac, "interface": d.interface, "ip": d.ip,
            "created_at": iso(d.created_at), "created_by": d.created_by}


async def _dhcp_interfaces() -> dict[str, str]:
    """DHCP server name → the interface it serves (usually the LAN bridge)."""
    return {s.get("name", ""): s.get("interface", "") for s in await mt.get("/ip/dhcp-server")}


@router.get("/wol")
async def wol_list(_: User = Depends(require_admin), db: Session = Depends(get_db), rt: Router = Depends(use_router)):
    """Saved computers of the selected router plus suggestions from its DHCP leases."""
    devices = db.query(WolDevice).filter(WolDevice.router_id == rt.id).order_by(WolDevice.name).all()
    out = {"devices": [_wol_out(d) for d in devices], "candidates": [], "interfaces": [], "error": None}
    try:
        servers = await _dhcp_interfaces()
        leases = await mt.get("/ip/dhcp-server/lease")
        bridges = [b.get("name") for b in await mt.get("/interface/bridge")]
    except MikroTikError as exc:
        out["error"] = str(exc)
        return out
    seen = set()
    for lease in leases:
        mac = (lease.get("active-mac-address") or lease.get("mac-address") or "").upper()
        if not MAC_RE.match(mac) or mac in seen:
            continue
        seen.add(mac)
        out["candidates"].append({
            "mac": mac, "ip": lease.get("active-address") or lease.get("address") or "",
            "name": (lease.get("comment") or lease.get("host-name") or "").strip()[:64],
            "interface": servers.get(lease.get("server", ""), ""),
            "online": lease.get("status") == "bound",
        })
    out["candidates"].sort(key=lambda c: (not c["name"], c["name"].lower(), c["ip"]))
    out["interfaces"] = sorted({i for i in [*servers.values(), *bridges] if i})
    return out


@router.post("/wol/devices")
def wol_add(body: WolDeviceIn, user: User = Depends(require_admin), db: Session = Depends(get_db),
            rt: Router = Depends(use_router)):
    mac = body.mac.strip().upper().replace("-", ":")
    if not MAC_RE.match(mac):
        raise HTTPException(400, tr("MAC inválido. Exemplo: AA:BB:CC:11:22:33"))
    ip = body.ip.strip()
    if ip:
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise HTTPException(400, tr("IP inválido"))
    if db.query(WolDevice).filter(WolDevice.router_id == rt.id, WolDevice.mac == mac).first():
        raise HTTPException(400, tr("Esse computador (MAC {mac}) já está na lista", mac=mac))
    device = WolDevice(router_id=rt.id, name=body.name.strip(), mac=mac, interface=body.interface.strip(), ip=ip,
                       created_by=user.username)
    db.add(device)
    db.commit()
    audit(db, user.username, "computador adicionado ao Wake-on-LAN", f"{rt.name}: {device.name} ({mac})")
    return _wol_out(device)


def _wol_or_404(db: Session, device_id: int, rt: Router) -> WolDevice:
    device = db.get(WolDevice, device_id)
    if not device or device.router_id != rt.id:
        raise HTTPException(404, tr("Computador não encontrado neste roteador"))
    return device


@router.delete("/wol/devices/{device_id}")
def wol_delete(device_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db),
               rt: Router = Depends(use_router)):
    device = _wol_or_404(db, device_id, rt)
    db.delete(device)
    db.commit()
    audit(db, user.username, "computador removido do Wake-on-LAN", f"{rt.name}: {device.name} ({device.mac})")
    return {"ok": True}


@router.post("/wol/devices/{device_id}/wake")
async def wol_wake(device_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db),
                   rt: Router = Depends(use_router)):
    """Asks the MikroTik to send the Wake-on-LAN "magic packet" to the computer's network card."""
    device = _wol_or_404(db, device_id, rt)
    interface = device.interface
    try:
        if not interface:  # not chosen: use the LAN the DHCP server serves
            interface = next((i for i in (await _dhcp_interfaces()).values() if i), "")
        data = {"mac": device.mac, **({"interface": interface} if interface else {})}
        await mt.command("/tool/wol", data)
    except MikroTikError as exc:
        if "not enough permissions" in str(exc):
            raise HTTPException(403, tr("O usuário do painel no MikroTik não tem permissão para o Wake-on-LAN. Use o 🌐 WebFig "
                                        "ou dê ao grupo wgpanel a política 'test'."))
        raise HTTPException(502, str(exc))
    audit(db, user.username, "computador ligado pela rede (Wake-on-LAN)", f"{rt.name}: {device.name} ({device.mac})")
    return {"ok": True, "interface": interface}


@router.get("/update/guide")
async def update_guide(_: User = Depends(require_admin)):
    """Ready-to-paste commands to update the panel to the newest version, for the way it is installed."""
    from ..services import update_guide as guide_service  # lazy: only needed when the window is opened
    return await guide_service.guide()


@router.get("/security")
async def security(request: Request, _: User = Depends(require_admin), rt: Router = Depends(use_router)):
    ports = {"8080", str(request.url.port or "")} - {""}
    update = auto_tasks.router_state(rt.id).get("update")
    findings = await router_tools.security_check(mt, request.client.host if request.client else "", ports, update)
    return {"findings": findings, "update": update}


# ------------------------------------------------------------------ atualização do RouterOS

@router.post("/update/check")
async def check_update(_: User = Depends(require_admin), rt: Router = Depends(use_router)):
    try:
        info = await router_tools.check_update(mt)
    except MikroTikError as exc:
        raise _mikrotik_error(exc)
    auto_tasks._update_state(rt.id, update=info, update_checked=auto_tasks._now().isoformat())
    return info


# ------------------------------------------------------------------ backups automáticos

@router.get("/backups")
def list_backups(_: User = Depends(require_admin), rt: Router = Depends(use_router), db: Session = Depends(get_db)):
    st = auto_tasks.router_state(rt.id)
    s = get_settings(db)
    return {
        "backups": auto_tasks.list_backups(rt.id),
        "last_check": st.get("backup_checked"),
        "error": st.get("backup_error"),
        "settings": {k: s[k] for k in ("auto_backup_enabled", "auto_backup_keep")},
    }


@router.post("/backups/run")
async def run_backup(user: User = Depends(require_admin), rt: Router = Depends(use_router),
                     db: Session = Depends(get_db)):
    try:
        res = await auto_tasks.run_backup(rt, int(get_settings(db).get("auto_backup_keep") or 14))
    except MikroTikError as exc:
        err = _mikrotik_error(exc)
        auto_tasks._update_state(rt.id, backup_error=err.detail)
        raise err
    if res["saved"]:
        audit(db, user.username, "backup automático salvo", f"{rt.name}: {res['name']}")
    return res


@router.get("/backups/{name}")
def download_backup(name: str, _: User = Depends(require_admin), rt: Router = Depends(use_router)):
    path = auto_tasks.backup_path(rt.id, name)
    if not path:
        raise HTTPException(404, tr("Backup não encontrado"))
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in rt.name)
    return PlainTextResponse(path.read_text("utf-8"), media_type="text/plain; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{safe}-{name}"'})


@router.get("/backups/{name}/diff")
def backup_diff(name: str, _: User = Depends(require_admin), rt: Router = Depends(use_router)):
    if not auto_tasks.backup_path(rt.id, name):
        raise HTTPException(404, tr("Backup não encontrado"))
    return PlainTextResponse(auto_tasks.diff_with_previous(rt.id, name) or tr("(sem diferenças)"))
