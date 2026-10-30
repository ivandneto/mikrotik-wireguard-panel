import ipaddress
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import NatRule, NatSample, User, audit, iso, utcnow
from ..i18n import tr
from ..mikrotik import mt
from ..security import current_user, get_db, require_admin
from ..services import collector, nat_ops
from ..services.nat_templates import TEMPLATES
from ..routerctx import current_router, use_router
from ..utils import TAG, parse_ports, split_list

router = APIRouter(prefix="/api/nat", tags=["nat"], dependencies=[Depends(use_router)])


class NatIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    template: str = ""
    protocol: Literal["tcp", "udp", "both"]
    external_port: str
    internal_ip: str
    internal_port: str = ""
    whitelist: str = ""
    health_check: bool = True
    expires_hours: float | None = Field(None, gt=0, le=24 * 365)
    force: bool = False


class NatUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    enabled: bool | None = None
    health_check: bool | None = None
    expires_hours: float | None = Field(None, ge=0, le=24 * 365)  # 0 = sem expiração


def rule_out(rule: NatRule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "template": rule.template,
        "protocol": rule.protocol,
        "external_port": rule.external_port,
        "internal_ip": rule.internal_ip,
        "internal_port": rule.internal_port or rule.external_port,
        "whitelist": split_list(rule.whitelist),
        "health_check": rule.health_check,
        "enabled": rule.enabled,
        "expired": rule.expired,
        "expires_at": iso(rule.expires_at),
        "created_at": iso(rule.created_at),
        "created_by": rule.created_by,
        "live": collector.state["nat"].get(rule.id),
    }


def _validate(body: NatIn):
    try:
        ext = parse_ports(body.external_port)
        if body.internal_port and body.internal_port.replace(" ", "") != body.external_port.replace(" ", ""):
            internal = parse_ports(body.internal_port)
            single = lambda ranges: len(ranges) == 1 and ranges[0][0] == ranges[0][1]
            if not (single(ext) and single(internal)):
                raise ValueError(tr("Porta interna diferente da externa só é suportada para uma porta única "
                                    "(deixe a porta interna em branco para usar as mesmas)"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    try:
        ip = ipaddress.ip_address(body.internal_ip.strip())
    except ValueError:
        raise HTTPException(400, tr("IP interno inválido"))
    if not ip.is_private:
        raise HTTPException(400, tr("O IP interno deve ser um endereço da sua rede local"))
    for item in split_list(body.whitelist):
        try:
            ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise HTTPException(400, tr("Endereço inválido na whitelist: {item}", item=item))


def _rule_or_404(db: Session, rule_id: int) -> NatRule:
    rule = db.get(NatRule, rule_id)
    if not rule or rule.router_id != current_router().id:
        raise HTTPException(404, tr("Regra não encontrada neste roteador"))
    return rule


@router.get("/templates")
def templates(_: User = Depends(current_user)):
    return TEMPLATES


@router.get("/rules")
def list_rules(_: User = Depends(current_user), db: Session = Depends(get_db)):
    rules = db.query(NatRule).filter(NatRule.router_id == current_router().id).order_by(NatRule.name).all()
    return [rule_out(r) for r in rules]


@router.get("/router-rules")
async def router_rules(_: User = Depends(current_user)):
    """Todas as regras dstnat que existem no MikroTik (inclusive as não criadas pelo painel)."""
    out = []
    for r in await mt.get(nat_ops.NAT_PATH):
        if r.get("chain") != "dstnat" or r.get("dynamic") == "true":
            continue
        out.append({
            "id": r[".id"],
            "comment": r.get("comment", ""),
            "managed": r.get("comment", "").startswith(TAG),
            "protocol": r.get("protocol", ""),
            "dst_port": r.get("dst-port", ""),
            "to_addresses": r.get("to-addresses", ""),
            "to_ports": r.get("to-ports", ""),
            "action": r.get("action"),
            "disabled": r.get("disabled") == "true",
            "bytes": int(r.get("bytes", 0)),
            "packets": int(r.get("packets", 0)),
        })
    return out


@router.post("/rules")
async def create_rule(body: NatIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    _validate(body)
    conflicts = await nat_ops.find_conflicts(body.protocol, body.external_port, [])
    if conflicts and not body.force:
        raise HTTPException(409, tr("Porta já usada por: {rules}", rules="; ".join(conflicts)))

    rule = NatRule(
        router_id=current_router().id,
        name=body.name.strip(), template=body.template, protocol=body.protocol,
        external_port=body.external_port.replace(" ", ""), internal_ip=body.internal_ip.strip(),
        internal_port=body.internal_port.replace(" ", ""), whitelist="\n".join(split_list(body.whitelist)),
        health_check=body.health_check, enabled=True, created_by=user.username,
        expires_at=utcnow() + timedelta(hours=body.expires_hours) if body.expires_hours else None,
    )
    db.add(rule)
    db.flush()  # precisa do id para o comentário no MikroTik
    try:
        rule.mikrotik_ids = await nat_ops.apply_rule(rule, current_router())
    except Exception:
        db.rollback()
        raise
    db.commit()
    audit(db, user.username, "porta liberada",
          f"{rule.name}: {rule.protocol} {rule.external_port} → {rule.internal_ip}")
    collector.trigger()
    return rule_out(rule)


@router.patch("/rules/{rule_id}")
async def update_rule(rule_id: int, body: NatUpdate, user: User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    rule = _rule_or_404(db, rule_id)
    s = current_router()
    if body.expires_hours is not None:
        rule.expires_at = utcnow() + timedelta(hours=body.expires_hours) if body.expires_hours else None
    if body.health_check is not None:
        rule.health_check = body.health_check
    if body.name is not None and body.name.strip() != rule.name:
        rule.name = body.name.strip()
        for item_id in rule.mikrotik_ids:
            await mt.set(nat_ops.NAT_PATH, item_id, {"comment": nat_ops.comment_for(rule)})
    if body.enabled is not None and body.enabled != rule.enabled:
        if body.enabled:
            if rule.expires_at and rule.expires_at <= utcnow():
                rule.expires_at = None
            rule.enabled, rule.expired = True, False
            if await nat_ops.missing_ids(rule):
                await nat_ops.remove_rule(rule)
                rule.mikrotik_ids = await nat_ops.apply_rule(rule, s)
            else:
                await nat_ops.set_disabled(rule, False)
        else:
            rule.enabled = False
            await nat_ops.set_disabled(rule, True)
    db.commit()
    audit(db, user.username, "liberação de porta alterada", f"{rule.name}: {body.model_dump(exclude_none=True)}")
    collector.trigger()
    return rule_out(rule)


@router.post("/rules/{rule_id}/reapply")
async def reapply_rule(rule_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Apaga e recria as regras no MikroTik com a configuração salva no painel."""
    rule = _rule_or_404(db, rule_id)
    await nat_ops.remove_rule(rule)
    rule.mikrotik_ids = await nat_ops.apply_rule(rule, current_router())
    db.commit()
    audit(db, user.username, "liberação de porta recriada", rule.name)
    collector.trigger()
    return rule_out(rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rule = _rule_or_404(db, rule_id)
    await nat_ops.remove_rule(rule)
    db.query(NatSample).filter(NatSample.rule_id == rule.id).delete()
    db.delete(rule)
    db.commit()
    collector.forget("nat", rule_id)
    audit(db, user.username, "liberação de porta removida",
          f"{rule.name}: {rule.protocol} {rule.external_port} → {rule.internal_ip}")
    return {"ok": True}
