"""Operações sobre os registros de roteadores (sem tocar no MikroTik remoto)."""
from ..db import NatRule, NatSample, Peer, PeerSample, Router, SystemSample, WgInterface
from ..mikrotik import forget_client
from . import auto_tasks, collector


def delete_router_records(db, router: Router):
    """Tira um roteador do painel com tudo que era dele (interfaces, clientes, portas, histórico)."""
    iface_ids = [i.id for i in db.query(WgInterface).filter(WgInterface.router_id == router.id).all()]
    if iface_ids:
        for peer in db.query(Peer).filter(Peer.interface_id.in_(iface_ids)).all():
            db.query(PeerSample).filter(PeerSample.peer_id == peer.id).delete()
            collector.forget("peers", peer.id)
            db.delete(peer)
        db.query(WgInterface).filter(WgInterface.id.in_(iface_ids)).delete(synchronize_session=False)
    for rule in db.query(NatRule).filter(NatRule.router_id == router.id).all():
        db.query(NatSample).filter(NatSample.rule_id == rule.id).delete()
        collector.forget("nat", rule.id)
        db.delete(rule)
    db.query(SystemSample).filter(SystemSample.router_id == router.id).delete()
    collector.state["routers"].pop(router.id, None)
    auto_tasks.delete_router_backups(router.id)
    forget_client(router.id)
    db.delete(router)
