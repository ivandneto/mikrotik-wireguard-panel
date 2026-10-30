"""Ferramentas feitas pelo próprio roteador: exportar a configuração, verificar atualização, ping,
traceroute e a checagem de segurança."""
import asyncio
import ipaddress
import re

from ..i18n import tr
from ..mikrotik import MikroTik, MikroTikError
from ..utils import split_list

EXPORT_NAME = "wgpanel-auto-export"
PERMISSION_FIX = "/user group set [find name=wgpanel] policy=read,write,api,rest-api,test,sensitive,ftp"


def needs_permission(exc: Exception) -> bool:
    return isinstance(exc, MikroTikError) and "not enough permissions" in str(exc)


def permission_error() -> str:
    return tr("O usuário do painel no MikroTik precisa da permissão 'ftp' (acesso a arquivos; não liga o serviço FTP). "
              "No terminal do MikroTik, como admin: {command}", command=PERMISSION_FIX)


# ------------------------------------------------------------------ exportação (backup automático)

async def export_config(client: MikroTik) -> str:
    """Gera um .rsc no roteador (sem senhas: o RouterOS esconde dados sensíveis), lê o conteúdo e apaga o arquivo."""
    await client.command("/export", {"file": EXPORT_NAME})
    fname = f"{EXPORT_NAME}.rsc"
    row = None
    for _ in range(20):  # o arquivo aparece alguns instantes depois do comando
        rows = await client.get("/file", name=fname)
        if rows and int(rows[0].get("size") or 0) > 0:
            row = rows[0]
            break
        await asyncio.sleep(0.5)
    if row is None:
        raise MikroTikError(tr("O MikroTik não gerou o arquivo de exportação"))
    try:
        text = await _read_file(client, fname, int(row.get("size") or 0))
        if text is None:
            text = row.get("contents") or ""
    finally:
        await client.remove("/file", row[".id"])
    if not text.strip():
        raise MikroTikError(tr("Não foi possível ler o arquivo de exportação do MikroTik (RouterOS 7.13 ou mais novo)"))
    return text


async def _read_file(client: MikroTik, name: str, size: int) -> str | None:
    """/file/read (RouterOS 7.13+) em pedaços; None se a versão não tiver o comando."""
    chunk, offset, parts = 32768, 0, []
    while offset < size:
        try:
            res = await client.command("/file/read", {"file": name, "offset": offset, "chunk-size": chunk})
        except MikroTikError as exc:
            if offset == 0 and not needs_permission(exc):
                return None
            raise
        data = (res[0] if isinstance(res, list) and res else res or {}).get("data", "")
        if not data:
            break
        parts.append(data)
        offset += len(data.encode("utf-8", errors="ignore"))
    return "".join(parts)


def normalize_export(text: str) -> str:
    """Tira a linha de data do cabeçalho ("# 2026-10-30 01:00:00 by RouterOS …") para comparar exportações."""
    lines = text.replace("\r\n", "\n").split("\n")
    return "\n".join(l for l in lines if not re.match(r"^#.*\bby RouterOS\b", l)).strip()


# ------------------------------------------------------------------ atualização do RouterOS

async def check_update(client: MikroTik) -> dict:
    """Pede ao roteador para consultar a MikroTik e devolve versão instalada/disponível."""
    error = None
    try:
        await client.command("/system/package/update/check-for-updates", {})
    except MikroTikError as exc:
        error = permission_error() if needs_permission(exc) else str(exc)
    info = await client.get("/system/package/update") or {}
    if isinstance(info, list):
        info = info[0] if info else {}
    installed, latest = info.get("installed-version", ""), info.get("latest-version", "")
    return {
        "installed": installed, "latest": latest, "channel": info.get("channel", ""),
        "status": info.get("status", ""), "error": error,
        "available": bool(latest and installed and latest != installed),
    }


# ------------------------------------------------------------------ ping / traceroute

HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")


def _ms(value: str | None) -> float | None:
    """'10ms759us' → 10.759 · '11.5' → 11.5"""
    if not value:
        return None
    m = re.fullmatch(r"(?:(\d+)s)?(?:(\d+)ms)?(?:(\d+)us)?", value)
    if m and any(m.groups()):
        s, ms, us = (int(g or 0) for g in m.groups())
        return round(s * 1000 + ms + us / 1000, 2)
    try:
        return float(value)
    except ValueError:
        return None


async def ping(client: MikroTik, address: str, count: int) -> dict:
    rows = await client.request("POST", "/ping", json={"address": address, "count": str(count)},
                                timeout=count * 2 + 15) or []
    replies = [{"seq": r.get("seq"), "host": r.get("host"), "time": _ms(r.get("time")), "ttl": r.get("ttl"),
                "status": r.get("status", "")} for r in rows]
    last = rows[-1] if rows else {}
    return {
        "replies": replies,
        "sent": int(last.get("sent") or 0), "received": int(last.get("received") or 0),
        "loss": int(last.get("packet-loss") or (100 if rows else 0)),
        "min": _ms(last.get("min-rtt")), "avg": _ms(last.get("avg-rtt")), "max": _ms(last.get("max-rtt")),
    }


async def traceroute(client: MikroTik, address: str) -> list[dict]:
    rows = await client.request("POST", "/tool/traceroute",
                                json={"address": address, "count": "3", "max-hops": "30"}, timeout=120) or []
    # o RouterOS devolve a tabela a cada rodada (".section"): fica só a última
    last = max((r.get(".section", "0") for r in rows), key=lambda s: int(s or 0), default="0")
    hops = [r for r in rows if r.get(".section", "0") == last]
    return [{"hop": i, "address": h.get("address", ""), "loss": h.get("loss", ""), "last": _ms(h.get("last")),
             "avg": _ms(h.get("avg")), "best": _ms(h.get("best")), "worst": _ms(h.get("worst")),
             "status": h.get("status", "")} for i, h in enumerate(hops, 1)]


# ------------------------------------------------------------------ checagem de segurança

def _finding(fid: str, level: str, title: str, detail: str = "", fix: str = "") -> dict:
    return {"id": fid, "level": level, "title": title, "detail": detail, "fix": fix}


def _is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_global


async def security_check(client: MikroTik, client_ip: str, panel_ports: set[str], update: dict | None) -> list[dict]:
    out = []
    nat, filters, services, users, dns = await asyncio.gather(
        client.get("/ip/firewall/nat"), client.get("/ip/firewall/filter"), client.get("/ip/service"),
        client.get("/user"), client.get("/ip/dns"), return_exceptions=True)

    # 1. o painel aparece para quem acessa?
    if _is_public(client_ip):
        out.append(_finding("panel_public", "fail", tr("Você está acessando o painel por um IP público ({ip})", ip=client_ip),
                            tr("O painel deve ser aberto só pela rede local ou pela VPN."),
                            tr("Remova o port forward do painel e acesse pela VPN.")))
    else:
        out.append(_finding("panel_public", "ok", tr("Este acesso ao painel vem da rede local ou da VPN")))

    # 2. port forward apontando para o painel
    if isinstance(nat, list):
        hits = [r for r in nat if r.get("action") == "dst-nat" and r.get("disabled") != "true" and (
            set(split_list(r.get("dst-port", ""))) & panel_ports or set(split_list(r.get("to-ports", ""))) & panel_ports
            or r.get("to-addresses") == "172.20.0.2")]
        if hits:
            out.append(_finding("nat_panel", "fail", tr("Existe port forward para a porta do painel"),
                                ", ".join(f"{r.get('protocol', '')} {r.get('dst-port', '')} → {r.get('to-addresses', '')}"
                                          f":{r.get('to-ports', '')}" for r in hits),
                                tr("Apague essas regras em IP → Firewall → NAT. O painel não pode ficar na internet.")))
        else:
            out.append(_finding("nat_panel", "ok", tr("Nenhum port forward para a porta do painel")))

    # 3. firewall de entrada bloqueia a internet?
    has_drop = False
    if isinstance(filters, list):
        # regra "drop tudo que vem da internet": sem porta/protocolo/origem, só pela interface (ou lista) de entrada
        has_drop = any(r.get("chain") == "input" and r.get("action") == "drop" and r.get("disabled") != "true"
                       and not (r.get("dst-port") or r.get("protocol") or r.get("src-address") or r.get("src-address-list"))
                       and (r.get("in-interface-list") in ("!LAN", "WAN") or r.get("in-interface"))
                       for r in filters)
        if has_drop:
            out.append(_finding("input_drop", "ok", tr("O firewall bloqueia acessos da internet ao roteador")))
        else:
            out.append(_finding("input_drop", "fail", tr("Não encontrei a regra que bloqueia a internet no firewall (chain input)"),
                                tr("Sem ela, os serviços do roteador (Winbox, WebFig, API, DNS) podem ficar abertos para a internet."),
                                '/ip firewall filter add chain=input action=drop in-interface-list=!LAN '
                                'comment="drop all not coming from LAN"'))

    # 4. serviços do roteador
    if isinstance(services, list):
        enabled = [s for s in services if s.get("disabled") != "true" and s.get("dynamic") != "true"]
        insecure = [s["name"] for s in enabled if s.get("name") in ("telnet", "ftp")]
        if insecure:
            out.append(_finding("insecure_services", "warn", tr("Serviços sem criptografia ligados: {names}", names=", ".join(insecure)),
                                tr("Telnet e FTP enviam senhas sem criptografia."),
                                "/ip service disable " + ",".join(insecure)))
        open_any = [s["name"] for s in enabled if s.get("name") not in ("telnet", "ftp")
                    and not (s.get("available-from") or s.get("address"))]
        if open_any:
            title = (tr("Protegidos pelo firewall, mas aceitam qualquer endereço: {names}", names=", ".join(open_any)) if has_drop
                     else tr("Serviços que aceitam qualquer endereço: {names}", names=", ".join(open_any)))
            out.append(_finding("services_any", "ok" if has_drop else "warn", title,
                                tr("Limitar cada serviço às suas redes é uma camada extra de proteção."),
                                tr("IP → Services → cada serviço → Available From: sua rede local e a faixa da VPN.")))
        else:
            out.append(_finding("services_any", "ok", tr("Os serviços do roteador só aceitam as redes permitidas")))

    # 5. usuários
    if isinstance(users, list):
        admin = [u for u in users if u.get("name") == "admin" and u.get("disabled") != "true"]
        if admin:
            out.append(_finding("admin_user", "warn", tr("O usuário padrão 'admin' está ativo"),
                                tr("É o primeiro nome que robôs tentam. Crie outro usuário administrador e desative ou renomeie o 'admin'."),
                                '/user add name=NEW-ADMIN group=full password="STRONG-PASSWORD"  →  /user disable admin'))
        else:
            out.append(_finding("admin_user", "ok", tr("Não existe usuário 'admin' ativo")))
        panel = [u for u in users if u.get("name") == "wgpanel"]
        if panel and not (panel[0].get("address") or "").strip():
            out.append(_finding("panel_user", "warn", tr("O usuário 'wgpanel' pode entrar de qualquer endereço"),
                                tr("Limite-o às redes de onde o painel acessa."),
                                "/user set [find name=wgpanel] address=192.168.88.0/24,10.10.0.0/24,172.20.0.0/24"))

    # 6. DNS aberto
    if isinstance(dns, (dict, list)):
        d = dns[0] if isinstance(dns, list) and dns else dns if isinstance(dns, dict) else {}
        if d.get("allow-remote-requests") == "true" and not has_drop:
            out.append(_finding("dns_open", "fail", tr("O DNS do roteador pode estar aberto para a internet"),
                                tr("Sem o bloqueio no firewall, o roteador vira um DNS aberto (usado em ataques)."),
                                tr("Crie a regra de bloqueio da chain input (item acima).")))

    # 7. RouterOS atualizado
    if update and update.get("installed"):
        if update.get("available"):
            out.append(_finding("routeros_update", "warn",
                                tr("Atualização do RouterOS disponível: {latest} (instalado {installed})",
                                   latest=update["latest"], installed=update["installed"]),
                                tr("Atualizações corrigem falhas de segurança. Faça um backup antes."),
                                "/system package update install"))
        elif update.get("latest"):
            out.append(_finding("routeros_update", "ok", tr("RouterOS atualizado ({version})", version=update["installed"])))
    return out
