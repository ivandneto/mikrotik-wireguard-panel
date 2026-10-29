import io
import ipaddress
import re
import unicodedata
from typing import Literal

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import Peer, PeerSample, User, WgInterface, audit, iso
from ..mikrotik import MikroTikError, mt
from ..security import current_user, decrypt, encrypt, get_db, require_admin
from ..services import collector
from ..settings_store import get_settings, save_settings
from ..utils import TAG, generate_keypair, generate_psk, is_private, split_list, validate_networks

router = APIRouter(prefix="/api/wireguard", tags=["wireguard"])

IFACE_PATH = "/interface/wireguard"
PEERS_PATH = "/interface/wireguard/peers"
ROUTE_PATH = "/ip/route"
FILTER_PATH = "/ip/firewall/filter"
ADDR_LIST_PATH = "/ip/firewall/address-list"
# MikroTiks de clientes em modo "só eu acesso": conexões novas vindas deles são bloqueadas aqui
ONE_WAY_LIST = "wgpanel-sites-one-way"


class InterfaceIn(BaseModel):
    interface: str = Field("wg-panel", pattern=r"^[A-Za-z0-9_.-]{1,32}$")
    description: str = ""
    listen_port: int = Field(13231, ge=1, le=65535)
    address: str = "10.10.0.1/24"
    dns: str = ""
    lan_networks: str = ""
    endpoint_remote: str = ""
    endpoint_local: str = ""
    add_to_lan_list: bool = True
    open_firewall: bool = True


class PeerIn(BaseModel):
    interface_id: int
    kind: Literal["device", "site"] = "device"
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    owner: str = ""
    tunnel_mode: Literal["split", "full"] = "split"
    ip: str | None = None
    remote_networks: str = ""
    site_access: Literal["one_way", "two_way"] = "one_way"


class ImportIn(BaseModel):
    ids: list[str] = Field(min_length=1)


class PeerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    description: str | None = None
    owner: str | None = None
    tunnel_mode: Literal["split", "full"] | None = None
    enabled: bool | None = None
    remote_networks: str | None = None
    site_access: Literal["one_way", "two_way"] | None = None


def peer_comment(name: str) -> str:
    return f"{TAG} {name}"


def peer_out(peer: Peer) -> dict:
    return {
        "id": peer.id,
        "interface_id": peer.interface_id,
        "kind": peer.kind,
        "name": peer.name,
        "description": peer.description,
        "owner": peer.owner,
        "ip": peer.ip,
        "public_key": peer.public_key,
        "tunnel_mode": peer.tunnel_mode,
        "remote_networks": split_list(peer.remote_networks),
        "site_access": peer.site_access,
        "enabled": peer.enabled,
        # importado sem chave: a chave privada só existe no dispositivo, então não há QR/.conf/script
        "has_config": bool(peer.private_key_enc),
        "created_at": iso(peer.created_at),
        "created_by": peer.created_by,
        "live": collector.state["peers"].get(peer.id),
    }


def iface_out(iface: WgInterface, router_rows: dict | None, peers: list[Peer]) -> dict:
    mine = [p for p in peers if p.interface_id == iface.id]
    row = (router_rows or {}).get(iface.name)
    return {
        "id": iface.id,
        "name": iface.name,
        "description": iface.description,
        "listen_port": iface.listen_port,
        "address": iface.address,
        "public_key": iface.public_key,
        "dns": iface.dns,
        "lan_networks": iface.lan_networks,
        "router": None if row is None else {
            "running": row.get("running") == "true",
            "disabled": row.get("disabled") == "true",
            "listen_port": row.get("listen-port"),
        },
        "peers_total": len(mine),
        "peers_online": sum(1 for p in mine if collector.state["peers"].get(p.id, {}).get("online")),
    }


def _iface_or_404(db: Session, iface_id: int) -> WgInterface:
    iface = db.get(WgInterface, iface_id)
    if not iface:
        raise HTTPException(404, "Interface não encontrada")
    return iface


def _peer_or_404(db: Session, peer_id: int) -> Peer:
    peer = db.get(Peer, peer_id)
    if not peer:
        raise HTTPException(404, "Cliente não encontrado")
    return peer


def _peer_iface(db: Session, peer: Peer) -> WgInterface:
    iface = db.get(WgInterface, peer.interface_id) if peer.interface_id else None
    if not iface:
        raise HTTPException(400, "O cliente não está ligado a nenhuma interface do painel")
    return iface


def _allowed_on_router(peer: Peer) -> str:
    """No MikroTik central: o IP do peer na VPN + (site) as redes atrás dele."""
    first = peer.ip if "/" in peer.ip else f"{peer.ip}/32"
    return ",".join([first, *split_list(peer.remote_networks)]) if peer.kind == "site" else first


async def _router_peer(peer: Peer) -> dict | None:
    for row in await mt.get(PEERS_PATH):
        if row.get("public-key") == peer.public_key:
            return row
    return None


async def _create_on_router(peer: Peer, iface: WgInterface, settings: dict) -> str:
    data = {
        "interface": iface.name,
        "public-key": peer.public_key,
        "allowed-address": _allowed_on_router(peer),
        "comment": peer_comment(peer.name),
        "disabled": peer.enabled is False,  # None (ainda não salvo) = ativo
    }
    if peer.preshared_key_enc:
        data["preshared-key"] = decrypt(peer.preshared_key_enc)
    if settings["wg_store_keys_on_router"] and peer.private_key_enc:
        data["private-key"] = decrypt(peer.private_key_enc)
    result = await mt.add(PEERS_PATH, data)
    return result[".id"]


async def _place_on_top(path: str, data: dict):
    rules = await mt.get(path)
    first = next((r[".id"] for r in rules if r.get("dynamic") != "true"), None)
    if first:
        await mt.command(f"{path}/add", {**data, "place-before": first})
    else:
        await mt.add(path, data)


async def _ensure_input_rule(port: int) -> bool:
    """Libera a porta UDP do WireGuard no firewall (chain input), no topo da lista."""
    comment = f"{TAG} WireGuard UDP {port}"
    if any(r.get("comment") == comment for r in await mt.get(FILTER_PATH)):
        return False
    await _place_on_top(FILTER_PATH, {"chain": "input", "action": "accept", "protocol": "udp",
                                      "dst-port": port, "comment": comment})
    return True


async def _ensure_one_way_rules():
    """Bloqueia conexões NOVAS vindas de sites "só eu acesso" (respostas continuam passando)."""
    existing = {r.get("comment") for r in await mt.get(FILTER_PATH)}
    for chain in ("forward", "input"):
        comment = f"{TAG} sites só-ida: bloqueia conexões novas vindas do cliente ({chain})"
        if comment not in existing:
            await _place_on_top(FILTER_PATH, {"chain": chain, "action": "drop", "connection-state": "new",
                                              "src-address-list": ONE_WAY_LIST, "comment": comment})


def _site_list_comment(peer: Peer) -> str:
    return f"{TAG} peer:{peer.id} {peer.name}"


async def _sync_site(peer: Peer, iface: WgInterface):
    """Rotas para as redes do cliente e a lista de bloqueio do modo "só eu acesso"."""
    for route_id in peer.route_ids:
        await mt.remove(ROUTE_PATH, route_id)
    peer.route_ids = []
    for entry in await mt.get(ADDR_LIST_PATH, list=ONE_WAY_LIST):
        if entry.get("comment", "").startswith(f"{TAG} peer:{peer.id} "):
            await mt.remove(ADDR_LIST_PATH, entry[".id"])
    if peer.kind != "site":
        return

    route_ids = []
    for net in split_list(peer.remote_networks):
        result = await mt.add(ROUTE_PATH, {"dst-address": net, "gateway": iface.name,
                                           "comment": f"{TAG} site {peer.name}"})
        route_ids.append(result[".id"])
    peer.route_ids = route_ids

    if peer.site_access == "one_way":
        await _ensure_one_way_rules()
        for address in [peer.ip.split("/")[0], *split_list(peer.remote_networks)]:
            await mt.add(ADDR_LIST_PATH, {"list": ONE_WAY_LIST, "address": address,
                                          "comment": _site_list_comment(peer)})


async def _remove_site(peer: Peer):
    kind = peer.kind
    peer.kind = "device"  # _sync_site com kind != site só limpa
    try:
        await _sync_site(peer, None)
    finally:
        peer.kind = kind


def _iface_networks(iface: WgInterface) -> list:
    return [ipaddress.ip_interface(iface.address).network,
            *(ipaddress.ip_network(n) for n in validate_networks(iface.lan_networks))]


def _validate_remote_networks(db: Session, iface: WgInterface, text: str, peer_id: int | None) -> str:
    try:
        nets = [ipaddress.ip_network(n) for n in validate_networks(text)]
    except ValueError as exc:
        raise HTTPException(400, f"Rede do cliente inválida: {exc}")
    if not nets:
        raise HTTPException(400, "Informe a rede local do MikroTik do cliente (ex.: 192.168.10.0/24)")
    for net in nets:
        for own in _iface_networks(iface):
            if net.overlaps(own):
                raise HTTPException(400, f"A rede do cliente {net} conflita com a sua rede {own}. As duas pontas "
                                         "precisam de faixas diferentes — mude a faixa da LAN do cliente "
                                         "(ex.: 192.168.10.0/24).")
        for other in db.query(Peer).filter(Peer.kind == "site").all():
            if other.id == peer_id:
                continue
            for other_net in split_list(other.remote_networks):
                if net.overlaps(ipaddress.ip_network(other_net)):
                    raise HTTPException(400, f"A rede {net} conflita com a do cliente {other.name} ({other_net})")
    return ", ".join(str(n) for n in nets)


# ------------------------------------------------------------------ interfaces

@router.get("/interfaces")
async def list_interfaces(_: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_settings(db)
    error, rows = None, None
    try:
        rows = {r["name"]: r for r in await mt.get(IFACE_PATH)}
    except MikroTikError as exc:
        error = str(exc)
    peers = db.query(Peer).all()
    return {
        "endpoint_remote": s["endpoint_remote"],
        "endpoint_local": s["endpoint_local"],
        "error": error,
        "interfaces": [iface_out(i, rows, peers) for i in db.query(WgInterface).order_by(WgInterface.id).all()],
    }


@router.get("/suggest")
async def suggest(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Sugestões para o assistente: endereços públicos/DDNS, IP local, redes LAN e interfaces para adotar."""
    out = {"remote": [], "local": [], "lan_networks": [], "existing_interfaces": [], "next_port": 13231,
           "next_address": "10.10.0.1/24"}
    try:
        cloud = await mt.get("/ip/cloud")
        if cloud.get("dns-name"):
            out["remote"].append(cloud["dns-name"])
        if cloud.get("public-address"):
            out["remote"].append(cloud["public-address"])
    except MikroTikError:
        pass
    addresses = await mt.get("/ip/address")
    wg_rows = await mt.get(IFACE_PATH)
    wg_names = {r["name"] for r in wg_rows}
    for addr in addresses:
        if addr.get("disabled") == "true" or addr.get("interface") in wg_names:
            continue
        iface = ipaddress.ip_interface(addr["address"])
        ip = str(iface.ip)
        if is_private(ip):
            out["local"].append(ip)
            out["lan_networks"].append(str(iface.network))
        elif ip not in out["remote"]:
            out["remote"].append(ip)

    managed = {i.name for i in db.query(WgInterface).all()}
    for row in wg_rows:
        if row["name"] in managed:
            continue
        addr = next((a["address"] for a in addresses if a.get("interface") == row["name"]), "")
        out["existing_interfaces"].append({"name": row["name"], "listen_port": row.get("listen-port"),
                                           "address": addr})

    # próxima porta e faixa livres, para facilitar criar a 2ª, 3ª interface...
    used_ports = {int(r.get("listen-port") or 0) for r in wg_rows}
    port = 13231
    while port in used_ports:
        port += 1
    out["next_port"] = port
    used_nets = [ipaddress.ip_interface(a["address"]).network for a in addresses]
    for third in range(10, 250, 10):
        candidate = ipaddress.ip_network(f"10.{third}.0.0/24")
        if not any(candidate.overlaps(n) for n in used_nets):
            out["next_address"] = f"10.{third}.0.1/24"
            break
    return out


@router.post("/interfaces")
async def setup_interface(body: InterfaceIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Cria uma interface nova, adota uma existente ou reconfigura uma do painel (pelo nome)."""
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
    record = db.query(WgInterface).filter(WgInterface.name == body.interface).first()
    for other in db.query(WgInterface).all():
        if other.id == (record.id if record else None):
            continue
        if other.listen_port == body.listen_port:
            raise HTTPException(400, f"A porta {body.listen_port} já é usada pela interface {other.name}")
        if ipaddress.ip_interface(other.address).network.overlaps(iface_addr.network):
            raise HTTPException(400, f"A faixa {iface_addr.network} conflita com a da interface {other.name}")

    steps = []
    existing = await mt.get(IFACE_PATH, name=body.interface)
    if existing:
        iface = existing[0]
        if iface.get("listen-port") != str(body.listen_port):
            await mt.set(IFACE_PATH, iface[".id"], {"listen-port": body.listen_port})
        steps.append(f"Interface {body.interface} já existia e foi reaproveitada")
    else:
        await mt.add(IFACE_PATH, {"name": body.interface, "listen-port": body.listen_port, "comment": TAG})
        steps.append(f"Interface {body.interface} criada (porta UDP {body.listen_port})")
    iface = (await mt.get(IFACE_PATH, name=body.interface))[0]

    addresses = await mt.get("/ip/address", interface=body.interface)
    if any(a.get("address") == str(iface_addr) for a in addresses):
        steps.append(f"Endereço {iface_addr} já estava configurado")
    else:
        await mt.add("/ip/address", {"address": str(iface_addr), "interface": body.interface, "comment": TAG})
        steps.append(f"Endereço {iface_addr} adicionado à interface")

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
                steps.append("Interface adicionada à lista LAN (os peers acessam a rede e a internet)")
        else:
            steps.append("Lista de interfaces 'LAN' não existe — ajuste o firewall manualmente se necessário")

    if record is None:
        record = WgInterface(name=body.interface)
        db.add(record)
    record.description = body.description.strip()
    record.listen_port = body.listen_port
    record.address = str(iface_addr)
    record.public_key = iface.get("public-key", "")
    record.dns = body.dns.strip() or str(iface_addr.ip)
    record.lan_networks = ", ".join(lan_networks)
    settings_update = {"wg_configured": True}
    if body.endpoint_remote.strip():
        settings_update["endpoint_remote"] = body.endpoint_remote.strip()
    if body.endpoint_local.strip():
        settings_update["endpoint_local"] = body.endpoint_local.strip()
    save_settings(db, settings_update)
    db.commit()
    audit(db, user.username, "interface WireGuard configurada", f"{body.interface}: " + "; ".join(steps))
    collector.trigger()
    return {"ok": True, "steps": steps, "id": record.id}


@router.delete("/interfaces/{iface_id}")
async def delete_interface(iface_id: int, remove_from_router: bool = False, user: User = Depends(require_admin),
                           db: Session = Depends(get_db)):
    iface = _iface_or_404(db, iface_id)
    if db.query(Peer).filter(Peer.interface_id == iface.id).count():
        raise HTTPException(400, "Remova (ou mova) os clientes desta interface antes de excluí-la")
    removed = []
    if remove_from_router:
        for path, flt in (("/interface/list/member", {"interface": iface.name}),
                          ("/ip/address", {"interface": iface.name})):
            for row in await mt.get(path, **flt):
                if row.get("comment", "").startswith(TAG):
                    await mt.remove(path, row[".id"])
        if not any(i.listen_port == iface.listen_port for i in db.query(WgInterface).all() if i.id != iface.id):
            for rule in await mt.get(FILTER_PATH):
                if rule.get("comment") == f"{TAG} WireGuard UDP {iface.listen_port}":
                    await mt.remove(FILTER_PATH, rule[".id"])
        for row in await mt.get(IFACE_PATH, name=iface.name):
            await mt.remove(IFACE_PATH, row[".id"])
            removed.append("interface removida do MikroTik")
    db.delete(iface)
    db.commit()
    audit(db, user.username, "interface WireGuard excluída", f"{iface.name} {'; '.join(removed)}")
    return {"ok": True}


# ------------------------------------------------------------------ peers

@router.get("/peers")
def list_peers(_: User = Depends(current_user), db: Session = Depends(get_db)):
    return [peer_out(p) for p in db.query(Peer).order_by(Peer.name).all()]


@router.get("/importable")
async def importable_peers(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Peers que existem no MikroTik mas ainda não estão no painel (criados pelo Winbox, por exemplo)."""
    known = {p.public_key for p in db.query(Peer).all()}
    managed = {i.name for i in db.query(WgInterface).all()}
    out = []
    for row in await mt.get(PEERS_PATH):
        if row.get("public-key") in known:
            continue
        out.append({
            "id": row[".id"],
            "interface": row.get("interface"),
            "interface_managed": row.get("interface") in managed,
            "name": _imported_name(row),
            "allowed_address": row.get("allowed-address", ""),
            "endpoint": row.get("current-endpoint-address") or row.get("endpoint-address") or "",
            "last_handshake": row.get("last-handshake"),
            "disabled": row.get("disabled") == "true",
            "has_key": bool(row.get("private-key")),
        })
    return out


def _imported_name(row: dict) -> str:
    comment = (row.get("comment") or "").removeprefix(TAG).strip()
    return (comment or row.get("name") or f"peer {row['.id']}")[:64]


@router.post("/import")
async def import_peers(body: ImportIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Traz peers existentes para o painel sem alterar nada no MikroTik."""
    rows = {r[".id"]: r for r in await mt.get(PEERS_PATH)}
    ifaces = {i.name: i for i in db.query(WgInterface).all()}
    known_keys = {p.public_key for p in db.query(Peer).all()}
    used_ips = {p.ip for p in db.query(Peer).all()}
    imported, errors = [], []
    for item_id in body.ids:
        row = rows.get(item_id)
        if not row or row.get("public-key") in known_keys:
            continue
        name = _imported_name(row)
        iface = ifaces.get(row.get("interface"))
        if not iface:
            errors.append(f"{name}: a interface {row.get('interface')} ainda não está no painel — "
                          "adote-a em '+ Nova interface' primeiro")
            continue
        allowed = [a.strip() for a in (row.get("allowed-address") or "").split(",") if a.strip()]
        if not allowed:
            errors.append(f"{name}: sem allowed-address, ignorado")
            continue
        ip = allowed[0].removesuffix("/32")
        if ip in used_ips:
            errors.append(f"{name}: IP {ip} já usado por outro cliente do painel")
            continue
        extra = allowed[1:]  # redes além do IP do peer: é um site (outro roteador)
        psk = row.get("preshared-key") or ""
        peer = Peer(
            interface_id=iface.id, kind="site" if extra else "device", remote_networks=", ".join(extra),
            site_access="two_way",  # não sabemos como estava: não bloqueia nada que já funcionava
            name=name, description=f"Importado do MikroTik (interface {iface.name})",
            ip=ip, public_key=row["public-key"],
            private_key_enc=encrypt(row["private-key"]) if row.get("private-key") else "",
            preshared_key_enc=encrypt(psk) if psk else "", tunnel_mode="split",
            enabled=row.get("disabled") != "true", mikrotik_id=item_id, created_by=user.username,
        )
        db.add(peer)
        used_ips.add(ip)
        imported.append(name)
    db.commit()
    if imported:
        audit(db, user.username, "clientes VPN importados do MikroTik", ", ".join(imported))
    collector.trigger()
    return {"imported": imported, "errors": errors}


@router.post("/peers")
async def create_peer(body: PeerIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings(db)
    iface = _iface_or_404(db, body.interface_id)
    remote_networks = _validate_remote_networks(db, iface, body.remote_networks, None) if body.kind == "site" else ""

    server = ipaddress.ip_interface(iface.address)
    used = {str(server.ip)} | {p.ip for p in db.query(Peer).all()}
    for row in await mt.get(PEERS_PATH, interface=iface.name):
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
            raise HTTPException(400, f"Não há mais IPs livres na faixa {server.network}")

    private_key, public_key = generate_keypair()
    peer = Peer(
        interface_id=iface.id, kind=body.kind, remote_networks=remote_networks, site_access=body.site_access,
        name=body.name.strip(), description=body.description, owner=body.owner,
        ip=ip, public_key=public_key, private_key_enc=encrypt(private_key),
        preshared_key_enc=encrypt(generate_psk()), tunnel_mode=body.tunnel_mode, enabled=True,
        created_by=user.username,
    )
    peer.mikrotik_id = await _create_on_router(peer, iface, s)
    db.add(peer)
    db.flush()  # id usado nos comentários das listas de firewall
    try:
        await _sync_site(peer, iface)
    except MikroTikError:
        await mt.remove(PEERS_PATH, peer.mikrotik_id)
        db.rollback()
        raise
    db.commit()
    label = "MikroTik remoto criado" if peer.kind == "site" else "cliente VPN criado"
    audit(db, user.username, label, f"{peer.name} ({peer.ip}) em {iface.name}")
    collector.trigger()
    return peer_out(peer)


@router.patch("/peers/{peer_id}")
async def update_peer(peer_id: int, body: PeerUpdate, user: User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    peer = _peer_or_404(db, peer_id)
    iface = _peer_iface(db, peer)
    changes = body.model_dump(exclude_none=True)
    if "remote_networks" in changes:
        if peer.kind != "site":
            changes.pop("remote_networks")
        else:
            changes["remote_networks"] = _validate_remote_networks(db, iface, changes["remote_networks"], peer.id)

    router_changes = {}
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        router_changes["comment"] = peer_comment(changes["name"])
    if "enabled" in changes:
        router_changes["disabled"] = not changes["enabled"]
    for key, value in changes.items():
        setattr(peer, key, value)
    if "remote_networks" in changes:
        router_changes["allowed-address"] = _allowed_on_router(peer)
    if router_changes:
        row = await _router_peer(peer)
        if row:
            await mt.set(PEERS_PATH, row[".id"], router_changes)
    if peer.kind == "site" and {"remote_networks", "site_access", "name"} & changes.keys():
        await _sync_site(peer, iface)
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
    await _remove_site(peer)
    db.query(PeerSample).filter(PeerSample.peer_id == peer.id).delete()
    db.delete(peer)
    db.commit()
    collector.forget("peers", peer_id)
    audit(db, user.username, "cliente VPN removido", f"{peer.name} ({peer.ip})")
    return {"ok": True}


@router.post("/peers/{peer_id}/reapply")
async def reapply_peer(peer_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Recria/corrige o peer (e as rotas do site) no MikroTik caso tenham sido mexidos por fora do painel."""
    peer = _peer_or_404(db, peer_id)
    iface = _peer_iface(db, peer)
    row = await _router_peer(peer)
    if row:
        message = "O cliente já existe no MikroTik"
        if peer.enabled and row.get("disabled") == "true":
            await mt.set(PEERS_PATH, row[".id"], {"disabled": False})
            message = "Cliente estava desativado no MikroTik e foi reativado"
    else:
        peer.mikrotik_id = await _create_on_router(peer, iface, get_settings(db))
        message = "Cliente recriado no MikroTik"
    if peer.kind == "site":
        await _sync_site(peer, iface)
        message += " (rotas e regras do site reaplicadas)"
    db.commit()
    audit(db, user.username, "cliente VPN reaplicado no MikroTik", f"{peer.name}: {message}")
    collector.trigger()
    return {"ok": True, "message": message}


@router.post("/peers/{peer_id}/regenerate")
async def regenerate_keys(peer_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Gera chaves novas (ex.: celular perdido). A configuração antiga deixa de funcionar."""
    peer = _peer_or_404(db, peer_id)
    iface = _peer_iface(db, peer)
    row = await _router_peer(peer)
    private_key, public_key = generate_keypair()
    psk = generate_psk()
    if row:
        changes = {"public-key": public_key, "preshared-key": psk}
        if get_settings(db)["wg_store_keys_on_router"]:
            changes["private-key"] = private_key
        await mt.set(PEERS_PATH, row[".id"], changes)
    peer.public_key, peer.private_key_enc, peer.preshared_key_enc = public_key, encrypt(private_key), encrypt(psk)
    if not row:
        peer.mikrotik_id = await _create_on_router(peer, iface, get_settings(db))
    db.commit()
    audit(db, user.username, "chaves do cliente VPN regeneradas", peer.name)
    collector.trigger()
    return peer_out(peer)


# ------------------------------------------------------------------ arquivos para o lado do cliente

def _endpoint_host(s: dict, endpoint: str) -> str:
    host = s["endpoint_remote"] if endpoint == "remote" else s["endpoint_local"]
    if not host:
        label = "remoto (IP público/DDNS)" if endpoint == "remote" else "local (IP do MikroTik na rede)"
        raise HTTPException(400, f"Configure o endereço {label} em Configurações antes de gerar o arquivo")
    return host


def _require_key(peer: Peer):
    if not peer.private_key_enc:
        raise HTTPException(400, "Cliente importado sem a chave privada: ela só existe no dispositivo, então não "
                                 "dá para gerar o arquivo. Use 🔑 Gerar novas chaves para criar um novo.")


def build_client_config(peer: Peer, iface: WgInterface, s: dict, endpoint: str) -> str:
    """Arquivo .conf / QR Code para celular e computador."""
    _require_key(peer)
    host = _endpoint_host(s, endpoint)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if peer.tunnel_mode == "full":
        allowed = "0.0.0.0/0, ::/0"
    else:
        allowed = ", ".join(dict.fromkeys(str(n) for n in _iface_networks(iface)))

    lines = ["[Interface]", f"PrivateKey = {decrypt(peer.private_key_enc)}", f"Address = {peer.ip}/32"]
    if iface.dns:
        lines.append(f"DNS = {iface.dns}")
    if s["wg_mtu"]:
        lines.append(f"MTU = {s['wg_mtu']}")
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {iface.public_key}",
        *([f"PresharedKey = {decrypt(peer.preshared_key_enc)}"] if peer.preshared_key_enc else []),
        f"AllowedIPs = {allowed}",
        f"Endpoint = {host}:{iface.listen_port}",
        f"PersistentKeepalive = {s['wg_keepalive']}",
    ]
    return "\n".join(lines) + "\n"


def build_site_script(peer: Peer, iface: WgInterface, s: dict, endpoint: str) -> str:
    """Script RouterOS v7 para colar no terminal do MikroTik do cliente."""
    _require_key(peer)
    host = _endpoint_host(s, endpoint)
    vpn_net = ipaddress.ip_interface(iface.address).network
    central = [str(n) for n in _iface_networks(iface)]
    lans = [str(n) for n in _iface_networks(iface)[1:]]
    keepalive = s["wg_keepalive"] or 25
    psk = f' preshared-key="{decrypt(peer.preshared_key_enc)}"' if peer.preshared_key_enc else ""
    two_way = peer.site_access == "two_way"
    lines = [
        f"# ===== WG Panel: túnel do MikroTik \"{peer.name}\" até a central =====",
        "# Cole no Terminal do MikroTik do CLIENTE (RouterOS v7). Rode uma única vez.",
        f"# Central: {host}:{iface.listen_port} ({iface.name})  |  IP deste roteador no túnel: {peer.ip}",
        f"# Acesso: {'mão dupla (os dois lados se acessam)' if two_way else 'só a central acessa este roteador e a rede dele'}",
        "",
        f'/interface wireguard add name=wg-central listen-port={iface.listen_port} '
        f'private-key="{decrypt(peer.private_key_enc)}" comment="WG Panel: tunel para a central"',
        f'/interface wireguard peers add interface=wg-central name=central public-key="{iface.public_key}"{psk} '
        f'endpoint-address={host} endpoint-port={iface.listen_port} allowed-address={",".join(central)} '
        f'persistent-keepalive={keepalive}s comment="WG Panel: central"',
        f'/ip address add address={peer.ip}/{vpn_net.prefixlen} interface=wg-central comment="WG Panel"',
    ]
    for lan in lans:
        lines.append(f'/ip route add dst-address={lan} gateway=wg-central comment="WG Panel: rede da central"')
    lines += [
        "",
        "# Trata o túnel como rede interna: libera o acesso da central ao roteador (Winbox) e à rede.",
        "# Se este roteador não usa a lista LAN da configuração padrão, crie regras accept para wg-central.",
        '/interface list member add list=LAN interface=wg-central comment="WG Panel"',
        "",
        ':log info "WG Panel: tunel wg-central configurado"',
        ':put "OK! Em instantes o painel deve mostrar este MikroTik como Online."',
    ]
    # só ASCII: o terminal do RouterOS não lida bem com acentos (nomes como "São João" viram "Sao Joao")
    text = "\n".join(lines) + "\n"
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _slug(name: str, fallback: str) -> str:
    return re.sub(r"[^A-Za-z0-9_=+.-]+", "-", name).strip("-") or fallback


@router.get("/peers/{peer_id}/config")
def download_config(peer_id: int, endpoint: Literal["remote", "local"] = "remote",
                    user: User = Depends(require_admin), db: Session = Depends(get_db)):
    peer = _peer_or_404(db, peer_id)
    iface = _peer_iface(db, peer)
    s = get_settings(db)
    if peer.kind == "site":
        text = build_site_script(peer, iface, s, endpoint)
        filename = f"{_slug(peer.name, f'site{peer.id}')}-mikrotik.rsc"
    else:
        text = build_client_config(peer, iface, s, endpoint)
        # O app do WireGuard no Windows aceita no máximo 32 caracteres [a-zA-Z0-9_=+.-]
        suffix = "-remoto" if endpoint == "remote" else "-local"
        filename = f"{_slug(peer.name, f'cliente{peer.id}')[:32 - len(suffix)]}{suffix}.conf"
    audit(db, user.username, "configuração VPN baixada", f"{peer.name} ({endpoint})")
    return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/peers/{peer_id}/script")
def site_script(peer_id: int, endpoint: Literal["remote", "local"] = "remote",
                user: User = Depends(require_admin), db: Session = Depends(get_db)):
    peer = _peer_or_404(db, peer_id)
    if peer.kind != "site":
        raise HTTPException(400, "Script RouterOS só existe para clientes do tipo 'Outro MikroTik'")
    text = build_site_script(peer, _peer_iface(db, peer), get_settings(db), endpoint)
    audit(db, user.username, "script do MikroTik remoto exibido", f"{peer.name} ({endpoint})")
    return PlainTextResponse(text)


@router.get("/peers/{peer_id}/qr")
def qr_code(peer_id: int, endpoint: Literal["remote", "local"] = "remote",
            user: User = Depends(require_admin), db: Session = Depends(get_db)):
    peer = _peer_or_404(db, peer_id)
    if peer.kind == "site":
        raise HTTPException(400, "MikroTik remoto não usa QR Code: use o script RouterOS")
    text = build_client_config(peer, _peer_iface(db, peer), get_settings(db), endpoint)
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    audit(db, user.username, "QR code VPN exibido", f"{peer.name} ({endpoint})")
    return Response(buf.getvalue(), media_type="image/svg+xml")
