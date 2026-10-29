"""Criação/remoção das regras dst-nat no MikroTik. O painel só mexe no que ele mesmo criou."""
from ..db import NatRule
from ..mikrotik import MikroTikError, mt
from ..utils import TAG, parse_ports, ports_overlap, split_list

NAT_PATH = "/ip/firewall/nat"
ADDR_PATH = "/ip/firewall/address-list"


def comment_for(rule: NatRule) -> str:
    return f"{TAG} nat:{rule.id} {rule.name}"


def address_list_name(rule: NatRule) -> str:
    return f"wgpanel-nat-{rule.id}"


def protocols_of(protocol: str) -> list[str]:
    return ["tcp", "udp"] if protocol == "both" else [protocol]


def match_params(router) -> dict:
    """De onde vem o tráfego que deve ser redirecionado."""
    mode, target = router.nat_match_mode, router.nat_in_target
    if mode == "wan_list" and target:
        return {"in-interface-list": target}
    if mode == "interface" and target:
        return {"in-interface": target}
    return {"dst-address-type": "local"}


async def _sync_address_list(rule: NatRule, addresses: list[str]):
    name = address_list_name(rule)
    for entry in await mt.get(ADDR_PATH, list=name):
        await mt.remove(ADDR_PATH, entry[".id"])
    for address in addresses:
        await mt.add(ADDR_PATH, {"list": name, "address": address, "comment": f"{TAG} {rule.name}"})


async def apply_rule(rule: NatRule, router) -> list[str]:
    """Cria as regras no MikroTik e devolve os IDs. Desfaz tudo se algo falhar no meio."""
    extra = match_params(router)
    whitelist = split_list(rule.whitelist)
    if whitelist:
        await _sync_address_list(rule, whitelist)
        extra["src-address-list"] = address_list_name(rule)

    created = []
    try:
        for proto in protocols_of(rule.protocol):
            data = {
                "chain": "dstnat",
                "action": "dst-nat",
                "protocol": proto,
                "dst-port": rule.external_port,
                "to-addresses": rule.internal_ip,
                "comment": comment_for(rule),
                **extra,
            }
            if rule.internal_port and rule.internal_port != rule.external_port:
                data["to-ports"] = rule.internal_port
            if rule.enabled is False:  # None (ainda não salvo) = ativa
                data["disabled"] = True
            result = await mt.add(NAT_PATH, data)
            created.append(result[".id"])
    except MikroTikError:
        for item_id in created:
            await mt.remove(NAT_PATH, item_id)
        raise
    return created


async def remove_rule(rule: NatRule):
    for item_id in rule.mikrotik_ids:
        await mt.remove(NAT_PATH, item_id)
    for entry in await mt.get(ADDR_PATH, list=address_list_name(rule)):
        await mt.remove(ADDR_PATH, entry[".id"])


async def set_disabled(rule: NatRule, disabled: bool):
    for item_id in rule.mikrotik_ids:
        await mt.set(NAT_PATH, item_id, {"disabled": disabled})


async def missing_ids(rule: NatRule) -> bool:
    existing = {r[".id"] for r in await mt.get(NAT_PATH)}
    ids = rule.mikrotik_ids
    return not ids or any(i not in existing for i in ids)


async def find_conflicts(protocol: str, external_port: str, ignore_ids: list[str]) -> list[str]:
    """Outras regras dstnat (do painel ou não) que já usam a mesma porta/protocolo."""
    wanted = parse_ports(external_port)
    protos = set(protocols_of(protocol))
    conflicts = []
    for r in await mt.get(NAT_PATH):
        if r.get("chain") != "dstnat" or r[".id"] in ignore_ids or r.get("disabled") == "true":
            continue
        proto, dport = r.get("protocol"), r.get("dst-port")
        if not dport or (proto and proto not in protos):
            continue
        try:
            if ports_overlap(wanted, parse_ports(dport)):
                conflicts.append(
                    f"{r.get('comment') or 'regra sem comentário'} ({proto or 'qualquer'} {dport} → {r.get('to-addresses', '?')})"
                )
        except ValueError:
            continue
    return conflicts
