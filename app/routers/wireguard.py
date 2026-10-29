import io
import ipaddress
import re
from typing import Literal

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import Peer, PeerSample, User, audit, iso
from ..mikrotik import MikroTikError, mt
from ..security import current_user, decrypt, encrypt, get_db, require_admin
from ..services import collector
from ..settings_store import get_settings, save_settings
from ..utils import TAG, generate_keypair, generate_psk, is_private, validate_networks

router = APIRouter(prefix="/api/wireguard", tags=["wireguard"])

PEERS_PATH = "/interface/wireguard/peers"


class SetupIn(BaseModel):
    interface: str = Field("wg-panel", pattern=r"^[A-Za-z0-9_.-]{1,32}$")
    listen_port: int = Field(13231, ge=1, le=65535)
    address: str = "10.10.0.1/24"
    dns: str = ""
    lan_networks: str = ""
    endpoint_remote: str = ""
    endpoint_local: str = ""
    add_to_lan_list: bool = True
    open_firewall: bool = True


class PeerIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    owner: str = ""
    tunnel_mode: Literal["split", "full"] = "split"
    ip: str | None = None


class PeerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    description: str | None = None
    owner: str | None = None
    tunnel_mode: Literal["split", "full"] | None = None
    enabled: bool | None = None


def peer_comment(name: str) -> str:
    return f"{TAG} {name}"


def peer_out(peer: Peer) -> dict:
    return {
        "id": peer.id,
        "name": peer.name,
        "description": peer.description,
        "owner": peer.owner,
        "ip": peer.ip,
        "public_key": peer.public_key,
        "tunnel_mode": peer.tunnel_mode,
        "enabled": peer.enabled,
        "created_at": iso(peer.created_at),
        "created_by": peer.created_by,
        "live": collector.state["peers"].get(peer.id),
    }


def _require_configured(settings: dict):
    if not settings["wg_configured"]:
        raise HTTPException(400, "Faça primeiro a configuração inicial do WireGuard")


def _peer_or_404(db: Session, peer_id: int) -> Peer:
    peer = db.get(Peer, peer_id)
    if not peer:
        raise HTTPException(404, "Cliente não encontrado")
    return peer


async def _router_peer(peer: Peer) -> dict | None:
    for row in await mt.get(PEERS_PATH):
        if row.get("public-key") == peer.public_key:
            return row
    return None


async def _create_on_router(peer: Peer, settings: dict) -> str:
    result = await mt.add(PEERS_PATH, {
        "interface": settings["wg_interface"],
        "public-key": peer.public_key,
        "preshared-key": decrypt(peer.preshared_key_enc),
        "allowed-address": f"{peer.ip}/32",
        "comment": peer_comment(peer.name),
        "disabled": not peer.enabled,
    })
    return result[".id"]


async def _ensure_input_rule(port: int) -> bool:
    """Libera a porta UDP do WireGuard no firewall (chain input), no topo da lista."""
    comment = f"{TAG} WireGuard UDP {port}"
    rules = await mt.get("/ip/firewall/filter")
    if any(r.get("comment") == comment for r in rules):
        return False
    data = {"chain": "input", "action": "accept", "protocol": "udp", "dst-port": port, "comment": comment}
    first = next((r[".id"] for r in rules if r.get("dynamic") != "true"), None)
    if first:
        await mt.command("/ip/firewall/filter/add", {**data, "place-before": first})
    else:
        await mt.add("/ip/firewall/filter", data)
    return True


@router.get("/status")
async def status(_: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_settings(db)
    info = {
        "configured": s["wg_configured"],
        "interface": s["wg_interface"],
        "listen_port": s["wg_listen_port"],
        "address": s["wg_address"],
        "public_key": s["wg_server_public_key"],
        "endpoint_remote": s["endpoint_remote"],
        "endpoint_local": s["endpoint_local"],
        "dns": s["wg_dns"],
        "lan_networks": s["wg_lan_networks"],
        "router_interface": None,
        "error": None,
    }
    if s["wg_configured"]:
        try:
            rows = await mt.get("/interface/wireguard", name=s["wg_interface"])
            if rows:
                info["router_interface"] = {
                    "running": rows[0].get("running") == "true",
                    "disabled": rows[0].get("disabled") == "true",
                    "listen_port": rows[0].get("listen-port"),
                }
        except MikroTikError as exc:
            info["error"] = str(exc)
    return info


@router.get("/suggest")
async def suggest(_: User = Depends(require_admin)):
    """Sugestões para o assistente: endereços públicos/DDNS, IP local e redes LAN."""
    out = {"remote": [], "local": [], "lan_networks": [], "existing_interfaces": []}
    try:
        cloud = await mt.get("/ip/cloud")
        if cloud.get("dns-name"):
            out["remote"].append(cloud["dns-name"])
        if cloud.get("public-address"):
            out["remote"].append(cloud["public-address"])
    except MikroTikError:
        pass
    for addr in await mt.get("/ip/address"):
        if addr.get("disabled") == "true" or addr.get("interface", "").startswith("wg"):
            continue
        iface = ipaddress.ip_interface(addr["address"])
        ip = str(iface.ip)
        if is_private(ip):
            out["local"].append(ip)
            out["lan_networks"].append(str(iface.network))
        elif ip not in out["remote"]:
            out["remote"].append(ip)
    out["existing_interfaces"] = [
        {"name": r["name"], "listen_port": r.get("listen-port")} for r in await mt.get("/interface/wireguard")
    ]
    return out


@router.post("/setup")
async def setup(body: SetupIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        iface_addr = ipaddress.ip_interface(body.address)
    except ValueError:
        raise HTTPException(400, "Endereço da VPN inválido. Exemplo: 10.10.0.1/24")
    if iface_addr.version != 4 or iface_addr.network.prefixlen > 29:
        raise HTTPException(400, "Use uma faixa IPv4 com pelo menos /29 (recomendado /24)")
    try:
        lan_networks = validate_networks(body.lan_networks)
    except ValueError as exc:
        raise HTTPException(400, f"Rede LAN inválida: {exc}")

    steps = []
    existing = await mt.get("/interface/wireguard", name=body.interface)
    if existing:
        iface = existing[0]
        if iface.get("listen-port") != str(body.listen_port):
            await mt.set("/interface/wireguard", iface[".id"], {"listen-port": body.listen_port})
        steps.append(f"Interface {body.interface} já existia e foi reaproveitada")
    else:
        await mt.add("/interface/wireguard", {"name": body.interface, "listen-port": body.listen_port, "comment": TAG})
        steps.append(f"Interface {body.interface} criada (porta UDP {body.listen_port})")
    iface = (await mt.get("/interface/wireguard", name=body.interface))[0]

    addresses = await mt.get("/ip/address", interface=body.interface)
    if any(a.get("address") == body.address for a in addresses):
        steps.append(f"Endereço {body.address} já estava configurado")
    else:
        await mt.add("/ip/address", {"address": body.address, "interface": body.interface, "comment": TAG})
        steps.append(f"Endereço {body.address} adicionado à interface")

    if body.open_firewall:
        created = await _ensure_input_rule(body.listen_port)
        steps.append(f"Firewall: porta UDP {body.listen_port} liberada" if created
                     else "Firewall: regra de liberação já existia")

    if body.add_to_lan_list:
        if await mt.get("/interface/list", name="LAN"):
            if await mt.get("/interface/list/member", list="LAN", interface=body.interface):
                steps.append("Interface já era membro da lista LAN")
            else:
                await mt.add("/interface/list/member", {"list": "LAN", "interface": body.interface, "comment": TAG})
                steps.append("Interface adicionada à lista LAN (clientes VPN acessam a rede e a internet)")
        else:
            steps.append("Lista de interfaces 'LAN' não existe — ajuste o firewall manualmente se necessário")

    save_settings(db, {
        "wg_configured": True,
        "wg_interface": body.interface,
        "wg_listen_port": body.listen_port,
        "wg_address": str(iface_addr),
        "wg_server_public_key": iface.get("public-key", ""),
        "wg_dns": body.dns.strip() or str(iface_addr.ip),
        "wg_lan_networks": ", ".join(lan_networks),
        "endpoint_remote": body.endpoint_remote.strip(),
        "endpoint_local": body.endpoint_local.strip(),
    })
    audit(db, user.username, "wireguard configurado", "; ".join(steps))
    return {"ok": True, "steps": steps}


@router.get("/peers")
def list_peers(_: User = Depends(current_user), db: Session = Depends(get_db)):
    return [peer_out(p) for p in db.query(Peer).order_by(Peer.name).all()]


@router.post("/peers")
async def create_peer(body: PeerIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings(db)
    _require_configured(s)
    server = ipaddress.ip_interface(s["wg_address"])
    used = {str(server.ip)} | {p.ip for p in db.query(Peer).all()}
    for row in await mt.get(PEERS_PATH, interface=s["wg_interface"]):
        for allowed in (row.get("allowed-address") or "").split(","):
            if allowed:
                used.add(allowed.split("/")[0])

    if body.ip:
        try:
            ip = ipaddress.ip_address(body.ip)
        except ValueError:
            raise HTTPException(400, "IP inválido")
        if ip not in server.network or str(ip) in used or ip in (server.network.network_address,
                                                                  server.network.broadcast_address):
            raise HTTPException(400, f"IP {ip} fora da faixa {server.network} ou já em uso")
        ip = str(ip)
    else:
        ip = next((str(h) for h in server.network.hosts() if str(h) not in used), None)
        if not ip:
            raise HTTPException(400, "Não há mais IPs livres na faixa da VPN")

    private_key, public_key = generate_keypair()
    peer = Peer(
        name=body.name.strip(), description=body.description, owner=body.owner,
        ip=ip, public_key=public_key, private_key_enc=encrypt(private_key),
        preshared_key_enc=encrypt(generate_psk()), tunnel_mode=body.tunnel_mode,
        created_by=user.username,
    )
    peer.mikrotik_id = await _create_on_router(peer, s)
    db.add(peer)
    db.commit()
    audit(db, user.username, "cliente VPN criado", f"{peer.name} ({peer.ip})")
    collector.trigger()
    return peer_out(peer)


@router.patch("/peers/{peer_id}")
async def update_peer(peer_id: int, body: PeerUpdate, user: User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    peer = _peer_or_404(db, peer_id)
    changes = body.model_dump(exclude_none=True)
    router_changes = {}
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        router_changes["comment"] = peer_comment(changes["name"])
    if "enabled" in changes:
        router_changes["disabled"] = not changes["enabled"]
    if router_changes:
        row = await _router_peer(peer)
        if row:
            await mt.set(PEERS_PATH, row[".id"], router_changes)
    for key, value in changes.items():
        setattr(peer, key, value)
    db.commit()
    audit(db, user.username, "cliente VPN alterado", f"{peer.name}: {', '.join(changes)}")
    collector.trigger()
    return peer_out(peer)


@router.delete("/peers/{peer_id}")
async def delete_peer(peer_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    peer = _peer_or_404(db, peer_id)
    row = await _router_peer(peer)
    if row:
        await mt.remove(PEERS_PATH, row[".id"])
    db.query(PeerSample).filter(PeerSample.peer_id == peer.id).delete()
    db.delete(peer)
    db.commit()
    collector.forget("peers", peer_id)
    audit(db, user.username, "cliente VPN removido", f"{peer.name} ({peer.ip})")
    return {"ok": True}


@router.post("/peers/{peer_id}/reapply")
async def reapply_peer(peer_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Recria o peer no MikroTik caso tenha sido apagado por fora do painel."""
    peer = _peer_or_404(db, peer_id)
    s = get_settings(db)
    _require_configured(s)
    if await _router_peer(peer):
        return {"ok": True, "message": "O cliente já existe no MikroTik"}
    peer.mikrotik_id = await _create_on_router(peer, s)
    db.commit()
    audit(db, user.username, "cliente VPN recriado no MikroTik", peer.name)
    collector.trigger()
    return {"ok": True, "message": "Cliente recriado no MikroTik"}


@router.post("/peers/{peer_id}/regenerate")
async def regenerate_keys(peer_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Gera chaves novas (ex.: celular perdido). A configuração antiga deixa de funcionar."""
    peer = _peer_or_404(db, peer_id)
    row = await _router_peer(peer)
    private_key, public_key = generate_keypair()
    psk = generate_psk()
    if row:
        await mt.set(PEERS_PATH, row[".id"], {"public-key": public_key, "preshared-key": psk})
    peer.public_key, peer.private_key_enc, peer.preshared_key_enc = public_key, encrypt(private_key), encrypt(psk)
    if not row:
        peer.mikrotik_id = await _create_on_router(peer, get_settings(db))
    db.commit()
    audit(db, user.username, "chaves do cliente VPN regeneradas", peer.name)
    collector.trigger()
    return peer_out(peer)


def build_client_config(peer: Peer, s: dict, endpoint: str) -> str:
    host = s["endpoint_remote"] if endpoint == "remote" else s["endpoint_local"]
    if not host:
        label = "remoto (IP público/DDNS)" if endpoint == "remote" else "local (IP do MikroTik na rede)"
        raise HTTPException(400, f"Configure o endereço {label} em Configurações antes de gerar o arquivo")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    server = ipaddress.ip_interface(s["wg_address"])
    if peer.tunnel_mode == "full":
        allowed = "0.0.0.0/0, ::/0"
    else:
        nets = [str(server.network)] + validate_networks(s["wg_lan_networks"])
        allowed = ", ".join(dict.fromkeys(nets))

    lines = ["[Interface]", f"PrivateKey = {decrypt(peer.private_key_enc)}", f"Address = {peer.ip}/32"]
    if s["wg_dns"]:
        lines.append(f"DNS = {s['wg_dns']}")
    if s["wg_mtu"]:
        lines.append(f"MTU = {s['wg_mtu']}")
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {s['wg_server_public_key']}",
        f"PresharedKey = {decrypt(peer.preshared_key_enc)}",
        f"AllowedIPs = {allowed}",
        f"Endpoint = {host}:{s['wg_listen_port']}",
        f"PersistentKeepalive = {s['wg_keepalive']}",
    ]
    return "\n".join(lines) + "\n"


def _filename(peer: Peer, endpoint: str) -> str:
    # O app do WireGuard no Windows aceita no máximo 32 caracteres [a-zA-Z0-9_=+.-]
    base = re.sub(r"[^A-Za-z0-9_=+.-]+", "-", peer.name).strip("-") or f"cliente{peer.id}"
    suffix = "-remoto" if endpoint == "remote" else "-local"
    return f"{base[:32 - len(suffix)]}{suffix}.conf"


@router.get("/peers/{peer_id}/config")
def download_config(peer_id: int, endpoint: Literal["remote", "local"] = "remote",
                    user: User = Depends(require_admin), db: Session = Depends(get_db)):
    peer = _peer_or_404(db, peer_id)
    text = build_client_config(peer, get_settings(db), endpoint)
    audit(db, user.username, "configuração VPN baixada", f"{peer.name} ({endpoint})")
    return PlainTextResponse(text, headers={
        "Content-Disposition": f'attachment; filename="{_filename(peer, endpoint)}"'
    })


@router.get("/peers/{peer_id}/qr")
def qr_code(peer_id: int, endpoint: Literal["remote", "local"] = "remote",
            user: User = Depends(require_admin), db: Session = Depends(get_db)):
    peer = _peer_or_404(db, peer_id)
    text = build_client_config(peer, get_settings(db), endpoint)
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    audit(db, user.username, "QR code VPN exibido", f"{peer.name} ({endpoint})")
    return Response(buf.getvalue(), media_type="image/svg+xml")
