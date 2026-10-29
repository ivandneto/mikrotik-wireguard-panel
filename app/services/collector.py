"""Coletor em segundo plano: lê todos os MikroTiks a cada POLL_INTERVAL, guarda histórico e dispara alertas."""
import asyncio
import logging
import time
from datetime import timedelta

from ..config import config
from ..db import (Event, NatRule, NatSample, Peer, PeerSample, Router, SessionLocal, SystemSample, WgInterface, iso,
                  utcnow)
from ..mikrotik import MikroTikError, client_for, mt, use
from ..security import decrypt, encrypt
from ..settings_store import get_settings
from ..utils import fmt_duration, parse_duration, to_int
from . import nat_ops
from .alerts import notify

logger = logging.getLogger("wgpanel.collector")

# Último estado conhecido (servido direto pela API, sem consultar o roteador a cada clique)
state = {
    "routers": {},  # router_id -> {online, error, resource, wan, updated}
    "peers": {},    # peer_id -> status
    "nat": {},      # rule_id -> status
}
_prev = {"wan": {}, "router_online": {}, "nat": {}, "peer_online": {}, "health": {}, "health_fails": {},
         "nat_missing": {}}
_wake = asyncio.Event()
_lock = asyncio.Lock()
_pending: list[tuple] = []  # alertas enviados só após o commit (evita travar o SQLite)
_last_prune = 0.0


def trigger():
    """Pede uma coleta imediata (após criar/alterar algo)."""
    _wake.set()


async def run_forever():
    while True:
        try:
            await collect()
        except Exception:
            logger.exception("Erro no coletor")
        try:
            await asyncio.wait_for(_wake.wait(), timeout=config.POLL_INTERVAL)
            await asyncio.sleep(1)  # agrupa várias alterações seguidas
        except asyncio.TimeoutError:
            pass
        _wake.clear()


def _queue(kind: str, title: str, message: str, level: str):
    _pending.append((kind, title, message, level))


async def collect():
    async with _lock:
        try:
            await _collect()
        finally:
            while _pending:
                await notify(*_pending.pop(0))


async def _collect():
    db = SessionLocal()
    try:
        settings = get_settings(db)
        router_ids = [r.id for r in db.query(Router).filter(Router.enabled.is_(True)).all()]
    finally:
        db.close()
    for gone in set(state["routers"]) - set(router_ids):
        state["routers"].pop(gone, None)
    multi = len(router_ids) > 1
    # cada roteador numa tarefa própria: um cliente offline (timeout) não atrasa os outros
    results = await asyncio.gather(*(_collect_router(rid, settings, multi) for rid in router_ids),
                                   return_exceptions=True)
    for rid, result in zip(router_ids, results):
        if isinstance(result, Exception):
            logger.error("Erro coletando o roteador %s: %r", rid, result)
    db = SessionLocal()
    try:
        _prune(db, settings)
    finally:
        db.close()


async def _collect_router(router_id: int, settings: dict, multi: bool):
    # autoflush desligado: as amostras só vão ao banco no commit final, sem segurar
    # uma transação de escrita aberta enquanto esperamos o roteador responder
    db = SessionLocal(autoflush=False)
    try:
        router = db.get(Router, router_id)
        use(client_for(router))  # vale só para esta tarefa (ContextVar)
        prefix = f"[{router.name}] " if multi else ""
        st = state["routers"].setdefault(router_id, {"online": None, "error": None, "resource": None,
                                                     "wan": None, "updated": None})
        now = utcnow()
        try:
            resource = await mt.get("/system/resource")
        except MikroTikError as exc:
            _router_down(router, st, prefix, str(exc))
            return
        _router_up(router, st, prefix)

        total_mem = to_int(resource.get("total-memory")) or 1
        res = {
            "board": resource.get("board-name"),
            "version": resource.get("version"),
            "uptime": parse_duration(resource.get("uptime")),
            "cpu": to_int(resource.get("cpu-load")),
            "mem_pct": round(100 - to_int(resource.get("free-memory")) * 100 / total_mem, 1),
            "total_memory": total_mem,
            "architecture": resource.get("architecture-name"),
        }
        st.update(online=True, error=None, resource=res, updated=iso(now))

        wan = await _collect_wan(router, st)
        await _collect_peers(db, router, settings, now, prefix)
        await _collect_nat(db, router, now, prefix)

        db.add(SystemSample(
            router_id=router_id, ts=now, cpu=res["cpu"], mem_pct=res["mem_pct"],
            wan_rx_bps=wan["rx_bps"] if wan else 0, wan_tx_bps=wan["tx_bps"] if wan else 0,
        ))
        db.commit()
    finally:
        db.close()


def _router_down(router: Router, st: dict, prefix: str, error: str):
    was_online = st["online"]
    st.update(online=False, error=error, updated=iso(utcnow()))
    # roteador de cliente recém-cadastrado ainda nem recebeu o script: só avisa se já esteve online
    if was_online is True or (was_online is None and router.is_main):
        _queue("router_down", f"{prefix}MikroTik inacessível", error, "error")


def _router_up(router: Router, st: dict, prefix: str):
    if st["online"] is False:
        _queue("router_up", f"{prefix}MikroTik voltou a responder", router.url, "success")


async def _detect_wan() -> str | None:
    """Interface de saída da rota padrão ativa (ex.: pppoe-out1, ether1)."""
    try:
        routes = await mt.get("/ip/route", **{"dst-address": "0.0.0.0/0"})
    except MikroTikError:
        return None  # sem permissão/menu: o gráfico WAN não pode derrubar a coleta inteira
    for route in routes:
        if route.get("active") != "true":
            continue
        immediate = route.get("immediate-gw", "")
        if "%" in immediate:  # "100.77.1.254%pppoe-out1"
            return immediate.split("%", 1)[1]
        gateway = route.get("gateway", "")
        if gateway and not gateway[0].isdigit():  # gateway já é o nome da interface
            return gateway
    return None


async def _collect_wan(router: Router, st: dict):
    name = router.wan_interface or await _detect_wan()
    if not name:
        st["wan"] = None
        return None
    rows = await mt.get("/interface", name=name)
    if not rows:
        return None
    rx, tx = to_int(rows[0].get("rx-byte")), to_int(rows[0].get("tx-byte"))
    t = time.monotonic()
    prev, _prev["wan"][router.id] = _prev["wan"].get(router.id), (t, rx, tx)
    if not prev or rx < prev[1] or tx < prev[2]:
        return None
    elapsed = t - prev[0]
    wan = {"interface": name, "auto": not router.wan_interface,
           "rx_bps": (rx - prev[1]) * 8 / elapsed, "tx_bps": (tx - prev[2]) * 8 / elapsed}
    st["wan"] = wan
    return wan


async def _collect_peers(db, router: Router, settings: dict, now, prefix: str):
    iface_ids = [i.id for i in db.query(WgInterface).filter(WgInterface.router_id == router.id).all()]
    peers = db.query(Peer).filter(Peer.interface_id.in_(iface_ids)).all() if iface_ids else []
    if not peers:
        return
    by_key = {r.get("public-key"): r for r in await mt.get("/interface/wireguard/peers")}
    threshold = int(settings["online_threshold"])
    for peer in peers:
        row = by_key.get(peer.public_key)
        handshake = parse_duration(row.get("last-handshake")) if row else None
        online = bool(row) and peer.enabled and handshake is not None and handshake <= threshold
        rx, tx = (to_int(row.get("rx")), to_int(row.get("tx"))) if row else (0, 0)
        endpoint = None
        if row and row.get("current-endpoint-address"):
            endpoint = f"{row['current-endpoint-address']}:{row.get('current-endpoint-port', '')}"
        if row and row[".id"] != peer.mikrotik_id:
            peer.mikrotik_id = row[".id"]
        if row:
            await _sync_private_key(peer, row, settings)

        state["peers"][peer.id] = {
            "exists": row is not None,
            "router_disabled": row.get("disabled") == "true" if row else None,
            "online": online,
            "last_handshake": handshake,
            "rx": rx,
            "tx": tx,
            "endpoint": endpoint,
        }

        previous = _prev["peer_online"].get(peer.id)
        label = "Site" if peer.kind == "site" else "VPN"
        if previous is not None and previous != online and peer.enabled:
            if online:
                _queue("peer_connect", f"{prefix}{label}: {peer.name} conectou",
                       f"IP na VPN {peer.ip} · origem {endpoint or 'desconhecida'}", "success")
            else:
                _queue("peer_disconnect", f"{prefix}{label}: {peer.name} desconectou",
                       f"Último handshake há {fmt_duration(handshake)}", "warn")
        _prev["peer_online"][peer.id] = online

        if row:
            db.add(PeerSample(ts=now, peer_id=peer.id, rx=rx, tx=tx, online=online))


async def _sync_private_key(peer: Peer, row: dict, settings: dict):
    """Painel -> roteador (se a opção estiver ligada) e roteador -> painel (peer importado sem chave)."""
    try:
        if not peer.private_key_enc and row.get("private-key"):
            peer.private_key_enc = encrypt(row["private-key"])
        elif settings["wg_store_keys_on_router"] and peer.private_key_enc and not row.get("private-key"):
            await mt.set("/interface/wireguard/peers", row[".id"], {"private-key": decrypt(peer.private_key_enc)})
            logger.info("Chave privada de %s guardada no MikroTik", peer.name)
    except MikroTikError as exc:
        logger.warning("Não foi possível sincronizar a chave de %s: %s", peer.name, exc)


async def _tcp_check(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _check_port(rule: NatRule) -> int:
    spec = rule.internal_port or rule.external_port
    return int(spec.split(",")[0].split("-")[0])


async def _collect_nat(db, router: Router, now, prefix: str):
    rules = db.query(NatRule).filter(NatRule.router_id == router.id).all()
    if not rules:
        return

    # Liberações temporárias vencidas
    for rule in rules:
        if rule.enabled and rule.expires_at and now >= rule.expires_at:
            await nat_ops.remove_rule(rule)
            rule.enabled, rule.expired, rule.mikrotik_ids = False, True, []
            db.commit()
            _queue("nat_expired", f"{prefix}Porta fechada: {rule.name}",
                   f"{rule.protocol.upper()} {rule.external_port} → {rule.internal_ip} expirou e foi removida", "info")

    rows = {r[".id"]: r for r in await mt.get(nat_ops.NAT_PATH)}

    # o teste de "serviço no ar" sai da máquina do painel: só faz sentido no roteador principal
    checkable = [r for r in rules if router.is_main and r.enabled and r.health_check and r.protocol in ("tcp", "both")]
    results = await asyncio.gather(*(_tcp_check(r.internal_ip, _check_port(r)) for r in checkable))
    health = dict(zip((r.id for r in checkable), results))

    for rule in rules:
        ids = rule.mikrotik_ids
        found = [rows[i] for i in ids if i in rows]
        total_bytes = sum(to_int(r.get("bytes")) for r in found)
        total_packets = sum(to_int(r.get("packets")) for r in found)
        missing = rule.enabled and (not ids or len(found) < len(ids))

        t = time.monotonic()
        prev = _prev["nat"].get(rule.id)
        _prev["nat"][rule.id] = (t, total_bytes, total_packets)
        bps = pps = None
        if prev and total_bytes >= prev[1]:
            elapsed = t - prev[0]
            bps = (total_bytes - prev[1]) * 8 / elapsed
            pps = (total_packets - prev[2]) / elapsed

        healthy = _health_transition(rule, health[rule.id], prefix) if rule.id in health else None

        was_missing = _prev["nat_missing"].get(rule.id, False)
        if missing and not was_missing:
            _queue("nat_missing", f"{prefix}Regra NAT ausente: {rule.name}",
                   "A regra foi removida ou alterada direto no MikroTik. Use 'Recriar' no painel.", "error")
        _prev["nat_missing"][rule.id] = missing

        state["nat"][rule.id] = {
            "missing": missing,
            "router_disabled": any(r.get("disabled") == "true" for r in found),
            "bytes": total_bytes,
            "packets": total_packets,
            "bps": bps,
            "pps": pps,
            "healthy": healthy,
            "last_check": iso(now) if rule.id in health else None,
        }
        if found:
            db.add(NatSample(ts=now, rule_id=rule.id, bytes=total_bytes, packets=total_packets, healthy=healthy))


def _health_transition(rule: NatRule, ok: bool, prefix: str):
    """Só considera 'fora do ar' depois de 2 falhas seguidas, para evitar alarme falso."""
    fails = 0 if ok else _prev["health_fails"].get(rule.id, 0) + 1
    _prev["health_fails"][rule.id] = fails
    last = _prev["health"].get(rule.id)
    current = True if ok else (False if fails >= 2 else last)
    _prev["health"][rule.id] = current
    target = f"{rule.internal_ip}:{_check_port(rule)}"
    if current is False and last is not False:
        _queue("service_down", f"{prefix}Serviço fora do ar: {rule.name}", f"{target} não responde", "error")
    elif current is True and last is False:
        _queue("service_up", f"{prefix}Serviço voltou: {rule.name}", f"{target} respondendo novamente", "success")
    return current


def _prune(db, settings: dict):
    global _last_prune
    if time.monotonic() - _last_prune < 3600:
        return
    _last_prune = time.monotonic()
    cutoff = utcnow() - timedelta(days=int(settings["retention_days"]))
    for model in (PeerSample, NatSample, SystemSample):
        db.query(model).filter(model.ts < cutoff).delete()
    db.query(Event).filter(Event.ts < utcnow() - timedelta(days=90)).delete()
    db.commit()


def forget(kind: str, item_id: int):
    """Limpa o estado em memória de um item removido."""
    state[kind].pop(item_id, None)
    buckets = ("peer_online",) if kind == "peers" else ("nat", "health", "health_fails", "nat_missing")
    for name in buckets:
        _prev[name].pop(item_id, None)
