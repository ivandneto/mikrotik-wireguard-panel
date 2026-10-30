from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import __version__
from ..config import config
from ..i18n import tr
from ..db import AuditLog, Event, NatRule, NatSample, Peer, PeerSample, Router, SystemSample, User, WgInterface, iso, utcnow
from ..routerctx import current_router, use_router
from ..security import current_user, get_db, require_admin
from ..services import auto_tasks, collector

router = APIRouter(prefix="/api/monitor", tags=["monitor"], dependencies=[Depends(use_router)])

_POINTS = 120  # pontos por gráfico


def _bucket_size(hours: int) -> int:
    return max(config.POLL_INTERVAL, int(hours * 3600 / _POINTS))


def _counter_deltas(rows, fields):
    """Converte contadores acumulados em incrementos, tratando reinício do roteador (contador zera)."""
    out, prev = [], None
    for row in rows:
        if prev is not None:
            delta = {}
            for f in fields:
                cur, old = getattr(row, f), getattr(prev, f)
                delta[f] = cur - old if cur >= old else cur
            out.append((row, delta))
        prev = row
    return out


def _bucketize_counters(rows, fields, start, hours):
    size = _bucket_size(hours)
    count = int(hours * 3600 // size) + 1
    buckets = [{f: 0 for f in fields} for _ in range(count)]
    for row, delta in _counter_deltas(rows, fields):
        idx = int((row.ts - start).total_seconds() // size)
        if 0 <= idx < count:
            for f in fields:
                buckets[idx][f] += delta[f]
    return size, [{"ts": iso(start + timedelta(seconds=i * size)), **b} for i, b in enumerate(buckets)]


@router.get("/overview")
def overview(_: User = Depends(current_user), db: Session = Depends(get_db)):
    rt = current_router()
    peers, rules = _router_peers(db, rt.id), db.query(NatRule).filter(NatRule.router_id == rt.id).all()
    live_peers, live_nat = collector.state["peers"], collector.state["nat"]
    empty = {"online": None, "error": None, "resource": None, "wan": None, "updated": None}
    return {
        "router": collector.state["routers"].get(rt.id, empty),
        "router_id": rt.id,
        "router_name": rt.name,
        "mikrotik_url": rt.url,
        "poll_interval": config.POLL_INTERVAL,
        "version": __version__,
        # resumo de todos os roteadores, para o seletor e o painel
        "routers": [{"id": r.id, "name": r.name, "is_main": r.is_main, "enabled": r.enabled,
                     "online": collector.state["routers"].get(r.id, {}).get("online"),
                     "error": collector.state["routers"].get(r.id, {}).get("error"),
                     "resource": collector.state["routers"].get(r.id, {}).get("resource"),
                     "update": auto_tasks.router_state(r.id).get("update")}
                    for r in db.query(Router).order_by(Router.is_main.desc(), Router.name).all()],
        "vpn": {
            "configured": db.query(WgInterface).filter(WgInterface.router_id == rt.id).count() > 0,
            "total": len(peers),
            "enabled": sum(p.enabled for p in peers),
            "online": sum(1 for p in peers if live_peers.get(p.id, {}).get("online")),
        },
        "nat": {
            "total": len(rules),
            "enabled": sum(r.enabled for r in rules),
            "healthy": sum(1 for r in rules if live_nat.get(r.id, {}).get("healthy") is True),
            "unhealthy": sum(1 for r in rules if live_nat.get(r.id, {}).get("healthy") is False),
            "missing": sum(1 for r in rules if live_nat.get(r.id, {}).get("missing")),
        },
    }


def _router_peers(db: Session, router_id: int) -> list[Peer]:
    ids = [i.id for i in db.query(WgInterface).filter(WgInterface.router_id == router_id).all()]
    return db.query(Peer).filter(Peer.interface_id.in_(ids)).all() if ids else []


@router.post("/refresh")
async def refresh(_: User = Depends(current_user)):
    await collector.collect()
    return {"ok": True}


@router.get("/system/history")
def system_history(hours: int = Query(24, ge=1, le=24 * 90), _: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    start = utcnow() - timedelta(hours=hours)
    rows = (db.query(SystemSample).filter(SystemSample.router_id == current_router().id, SystemSample.ts >= start)
            .order_by(SystemSample.ts).all())
    size = _bucket_size(hours)
    buckets: dict[int, list] = {}
    for row in rows:
        buckets.setdefault(int((row.ts - start).total_seconds() // size), []).append(row)
    points = []
    for idx in sorted(buckets):
        group = buckets[idx]
        n = len(group)
        points.append({
            "ts": iso(start + timedelta(seconds=idx * size)),
            "cpu": round(sum(r.cpu for r in group) / n, 1),
            "mem_pct": round(sum(r.mem_pct for r in group) / n, 1),
            "wan_rx_bps": sum(r.wan_rx_bps for r in group) / n,
            "wan_tx_bps": sum(r.wan_tx_bps for r in group) / n,
        })
    return {"points": points}


@router.get("/peers/{peer_id}/history")
def peer_history(peer_id: int, hours: int = Query(24, ge=1, le=24 * 90), _: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    peer = db.get(Peer, peer_id)
    if not peer:
        raise HTTPException(404, tr("Cliente não encontrado"))
    start = utcnow() - timedelta(hours=hours)
    rows = (db.query(PeerSample).filter(PeerSample.peer_id == peer_id, PeerSample.ts >= start)
            .order_by(PeerSample.ts).all())
    size, points = _bucketize_counters(rows, ("rx", "tx"), start, hours)
    online_samples = sum(r.online for r in rows)
    return {
        "name": peer.name,
        "bucket_seconds": size,
        "points": points,
        "total_rx": sum(p["rx"] for p in points),
        "total_tx": sum(p["tx"] for p in points),
        "online_pct": round(online_samples * 100 / len(rows), 1) if rows else 0,
    }


@router.get("/nat/{rule_id}/history")
def nat_history(rule_id: int, hours: int = Query(24, ge=1, le=24 * 90), _: User = Depends(current_user),
                db: Session = Depends(get_db)):
    rule = db.get(NatRule, rule_id)
    if not rule:
        raise HTTPException(404, tr("Regra não encontrada"))
    start = utcnow() - timedelta(hours=hours)
    rows = (db.query(NatSample).filter(NatSample.rule_id == rule_id, NatSample.ts >= start)
            .order_by(NatSample.ts).all())
    size, points = _bucketize_counters(rows, ("bytes", "packets"), start, hours)
    checked = [r.healthy for r in rows if r.healthy is not None]
    return {
        "name": rule.name,
        "bucket_seconds": size,
        "points": points,
        "total_bytes": sum(p["bytes"] for p in points),
        "total_packets": sum(p["packets"] for p in points),
        "uptime_pct": round(sum(checked) * 100 / len(checked), 1) if checked else None,
    }


@router.get("/usage")
def usage(days: int = Query(30, ge=1, le=365), _: User = Depends(current_user), db: Session = Depends(get_db)):
    """Consumo total por cliente VPN no período."""
    start = utcnow() - timedelta(days=days)
    out = []
    for peer in sorted(_router_peers(db, current_router().id), key=lambda p: p.name.lower()):
        rows = (db.query(PeerSample).filter(PeerSample.peer_id == peer.id, PeerSample.ts >= start)
                .order_by(PeerSample.ts).all())
        deltas = _counter_deltas(rows, ("rx", "tx"))
        last_online = next((r.ts for r in reversed(rows) if r.online), None)
        out.append({
            "peer_id": peer.id,
            "name": peer.name,
            "owner": peer.owner,
            "rx": sum(d["rx"] for _, d in deltas),
            "tx": sum(d["tx"] for _, d in deltas),
            "last_seen": iso(last_online),
        })
    return out


@router.get("/events")
def events(limit: int = Query(100, ge=1, le=1000), _: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Event).order_by(Event.id.desc()).limit(limit).all()
    return [{"ts": iso(e.ts), "level": e.level, "kind": e.kind, "message": e.message} for e in rows]


@router.get("/audit")
def audit_log(limit: int = Query(200, ge=1, le=2000), _: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
    return [{"ts": iso(a.ts), "username": a.username, "action": a.action, "detail": a.detail} for a in rows]
