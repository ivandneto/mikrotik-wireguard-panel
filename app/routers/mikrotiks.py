"""Cadastro dos MikroTiks gerenciados pelo painel (tela Roteadores)."""
import asyncio
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import NatRule, Peer, Router, User, WgInterface, audit, iso
from ..mikrotik import MikroTik, MikroTikError, client_for, forget_client
from ..security import current_user, decrypt, encrypt, get_db, require_admin
from ..services import collector
from ..services.router_ops import delete_router_records

router = APIRouter(prefix="/api/routers", tags=["routers"])

PROBE_PORTS = (80, 8080, 8888, 81, 8081, 8000)


class RouterIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    url: str = ""
    username: str = ""
    password: str | None = None  # vazio na edição = mantém a atual
    verify_tls: bool = False
    enabled: bool = True
    endpoint_remote: str = ""
    endpoint_local: str = ""
    nat_match_mode: Literal["wan_list", "interface", "dst_local"] = "wan_list"
    nat_in_target: str = "WAN"
    wan_interface: str = ""


def router_out(r: Router, db: Session) -> dict:
    iface_ids = [i.id for i in db.query(WgInterface).filter(WgInterface.router_id == r.id).all()]
    peers = db.query(Peer).filter(Peer.interface_id.in_(iface_ids)).all() if iface_ids else []
    site = db.get(Peer, r.site_peer_id) if r.site_peer_id else None
    return {
        "id": r.id,
        "name": r.name,
        "url": r.url,
        "username": r.username,
        "is_main": r.is_main,
        "enabled": r.enabled,
        "verify_tls": r.verify_tls,
        "site_peer_id": r.site_peer_id,
        "site_name": site.name if site else None,
        "endpoint_remote": r.endpoint_remote,
        "endpoint_local": r.endpoint_local,
        "nat_match_mode": r.nat_match_mode,
        "nat_in_target": r.nat_in_target,
        "wan_interface": r.wan_interface,
        "created_at": iso(r.created_at),
        "live": collector.state["routers"].get(r.id),
        "interfaces": len(iface_ids),
        "peers_total": len(peers),
        "peers_online": sum(1 for p in peers if collector.state["peers"].get(p.id, {}).get("online")),
        "nat_total": db.query(NatRule).filter(NatRule.router_id == r.id).count(),
    }


def _router_or_404(db: Session, router_id: int) -> Router:
    r = db.get(Router, router_id)
    if not r:
        raise HTTPException(404, "Roteador não encontrado")
    return r


def _validate_url(url: str) -> str:
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(400, "URL inválida. Exemplo: http://10.20.0.2:80 ou https://192.168.10.1")
    return url


@router.get("")
def list_routers(_: User = Depends(current_user), db: Session = Depends(get_db)):
    routers = db.query(Router).order_by(Router.is_main.desc(), Router.name).all()
    return [router_out(r, db) for r in routers]


@router.post("")
def create_router(body: RouterIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if not body.password:
        raise HTTPException(400, "Informe a senha do usuário da API")
    r = Router(name=body.name.strip(), url=_validate_url(body.url), username=body.username.strip() or "wgpanel",
               password_enc=encrypt(body.password), verify_tls=body.verify_tls, enabled=body.enabled,
               endpoint_remote=body.endpoint_remote.strip(), endpoint_local=body.endpoint_local.strip(),
               nat_match_mode=body.nat_match_mode, nat_in_target=body.nat_in_target.strip(),
               wan_interface=body.wan_interface.strip())
    db.add(r)
    db.commit()
    audit(db, user.username, "roteador adicionado", f"{r.name} ({r.url})")
    collector.trigger()
    return router_out(r, db)


@router.patch("/{router_id}")
def update_router(router_id: int, body: RouterIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    r = _router_or_404(db, router_id)
    r.name = body.name.strip()
    r.endpoint_remote, r.endpoint_local = body.endpoint_remote.strip(), body.endpoint_local.strip()
    r.nat_match_mode, r.nat_in_target = body.nat_match_mode, body.nat_in_target.strip()
    r.wan_interface = body.wan_interface.strip()
    if not r.is_main:  # o principal usa sempre o .env
        r.url = _validate_url(body.url)
        r.username = body.username.strip() or r.username
        r.verify_tls, r.enabled = body.verify_tls, body.enabled
        if body.password:
            r.password_enc = encrypt(body.password)
    db.commit()
    forget_client(r.id)
    audit(db, user.username, "roteador alterado", r.name)
    collector.trigger()
    return router_out(r, db)


@router.delete("/{router_id}")
def delete_router(router_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    r = _router_or_404(db, router_id)
    if r.is_main:
        raise HTTPException(400, "O roteador principal não pode ser removido (ele vem do arquivo .env)")
    name = r.name
    delete_router_records(db, r)
    db.commit()
    audit(db, user.username, "roteador removido do painel", name)
    return {"ok": True}


@router.post("/{router_id}/test")
async def test_router(router_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Testa a conexão. Para MikroTiks de clientes (pelo túnel), tenta também as portas mais comuns do www."""
    r = _router_or_404(db, router_id)
    try:
        res = await client_for(r).get("/system/resource")
        return {"ok": True, "message": f"Conectado: {res.get('board-name')} · RouterOS {res.get('version')}"}
    except MikroTikError as exc:
        first_error = str(exc)
        if exc.status == 401 or r.is_main:
            return {"ok": False, "message": first_error}

    parsed = urlparse(r.url)
    password = decrypt(r.password_enc) if r.password_enc else ""

    async def probe(port: int):
        client = MikroTik(f"{parsed.scheme}://{parsed.hostname}:{port}", r.username, password, r.verify_tls,
                          label=r.name, timeout=4)
        try:
            return port, await client.get("/system/resource")
        except MikroTikError:
            return port, None
        finally:
            await client.close()

    # todas as portas ao mesmo tempo: num roteador offline o teste não demora minutos
    ports = [p for p in PROBE_PORTS if p != (parsed.port or 80)]
    for port, res in await asyncio.gather(*(probe(p) for p in ports)):
        if res is None:
            continue
        url = f"{parsed.scheme}://{parsed.hostname}:{port}"
        r.url = url
        db.commit()
        forget_client(r.id)
        collector.trigger()
        audit(db, user.username, "roteador: porta da API detectada", f"{r.name} → {url}")
        return {"ok": True, "message": f"Encontrado na porta {port} e salvo: {res.get('board-name')} · "
                                       f"RouterOS {res.get('version')}"}
    return {"ok": False, "message": first_error}
