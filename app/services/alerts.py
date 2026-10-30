"""Envio de alertas para Telegram e Discord. Todo evento também fica registrado no banco."""
import asyncio
import html
import logging
from datetime import datetime, timezone

import httpx

from ..db import SessionLocal, log_event
from ..settings_store import get_settings

logger = logging.getLogger("wgpanel.alerts")

EVENT_LABELS = {
    "peer_connect": "Cliente VPN conectou",
    "peer_disconnect": "Cliente VPN desconectou",
    "service_down": "Serviço liberado no NAT parou de responder",
    "service_up": "Serviço liberado no NAT voltou",
    "nat_missing": "Regra NAT sumiu do MikroTik",
    "nat_expired": "Liberação temporária expirou",
    "router_down": "MikroTik inacessível",
    "router_up": "MikroTik voltou",
    "router_update": "Atualização do RouterOS disponível",
    "config_changed": "Configuração do MikroTik mudou (backup automático salvo)",
}
_COLORS = {"success": 0x22C55E, "info": 0x3B82F6, "warn": 0xF59E0B, "error": 0xEF4444}
_ICONS = {"success": "✅", "info": "ℹ️", "warn": "⚠️", "error": "🚨"}


async def send_telegram(token: str, chat_id: str, title: str, message: str, level: str):
    text = f"{_ICONS[level]} <b>{html.escape(title)}</b>\n{html.escape(message)}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Telegram recusou: {resp.text[:200]}")


async def send_discord(webhook: str, title: str, message: str, level: str):
    payload = {
        "username": "WG Panel",
        "embeds": [{
            "title": f"{_ICONS[level]} {title}",
            "description": message,
            "color": _COLORS[level],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "MikroTik WG Panel"},
        }],
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(webhook, json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"Discord recusou: {resp.text[:200]}")


async def dispatch(cfg: dict, title: str, message: str, level: str) -> dict[str, str | None]:
    """Envia para os canais ativos. Retorna {canal: erro ou None}."""
    jobs = {}
    if cfg.get("telegram_enabled") and cfg.get("telegram_token") and cfg.get("telegram_chat_id"):
        jobs["telegram"] = send_telegram(cfg["telegram_token"], cfg["telegram_chat_id"], title, message, level)
    if cfg.get("discord_enabled") and cfg.get("discord_webhook"):
        jobs["discord"] = send_discord(cfg["discord_webhook"], title, message, level)
    results = await asyncio.gather(*jobs.values(), return_exceptions=True)
    return {name: (str(r) if isinstance(r, Exception) else None) for name, r in zip(jobs, results)}


async def notify(kind: str, title: str, message: str, level: str = "info"):
    db = SessionLocal()
    try:
        log_event(db, level, kind, f"{title} — {message}")
        cfg = get_settings(db)["alerts"]
    finally:
        db.close()
    if not cfg["events"].get(kind, False):
        return
    for channel, error in (await dispatch(cfg, title, message, level)).items():
        if error:
            logger.warning("Falha ao enviar alerta via %s: %s", channel, error)
