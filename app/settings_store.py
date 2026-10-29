"""Configurações editáveis pelo painel, guardadas no banco (chave -> JSON)."""
import copy
import json

from .db import Setting

DEFAULTS = {
    # WireGuard (preenchido pelo assistente de configuração inicial)
    "wg_configured": False,
    "wg_interface": "wg-panel",
    "wg_listen_port": 13231,
    "wg_address": "10.10.0.1/24",
    "wg_server_public_key": "",
    "wg_dns": "",
    "wg_lan_networks": "192.168.88.0/24",
    "wg_keepalive": 25,
    "wg_mtu": 0,
    # Endereços que os clientes usam para chegar no MikroTik
    "endpoint_remote": "",  # IP fixo ou DDNS (ex.: xxxx.sn.mynetname.net)
    "endpoint_local": "",   # IP do MikroTik na rede local (ex.: 192.168.88.1)
    # NAT: como identificar o tráfego vindo da internet
    "nat_match_mode": "wan_list",  # wan_list | interface | dst_local
    "nat_in_target": "WAN",        # nome da interface-list ou da interface
    # Monitoramento
    "wan_interface": "",
    "online_threshold": 180,
    "retention_days": 30,
    # Alertas
    "alerts": {
        "telegram_enabled": False,
        "telegram_token": "",
        "telegram_chat_id": "",
        "discord_enabled": False,
        "discord_webhook": "",
        "events": {
            "peer_connect": True,
            "peer_disconnect": True,
            "service_down": True,
            "service_up": True,
            "nat_missing": True,
            "nat_expired": True,
            "router_down": True,
            "router_up": True,
        },
    },
}


def _merge(dst: dict, src: dict) -> dict:
    for key, value in src.items():
        if isinstance(dst.get(key), dict) and isinstance(value, dict):
            _merge(dst[key], value)
        else:
            dst[key] = value
    return dst


def get_settings(db) -> dict:
    data = copy.deepcopy(DEFAULTS)
    for row in db.query(Setting).all():
        try:
            value = json.loads(row.value)
        except ValueError:
            continue
        _merge(data, {row.key: value})
    return data


def save_settings(db, values: dict):
    current = get_settings(db)
    for key, value in values.items():
        if key not in DEFAULTS:
            continue
        if isinstance(DEFAULTS[key], dict) and isinstance(value, dict):
            value = _merge(current[key], value)
        row = db.get(Setting, key)
        if row:
            row.value = json.dumps(value)
        else:
            db.add(Setting(key=key, value=json.dumps(value)))
    db.commit()
