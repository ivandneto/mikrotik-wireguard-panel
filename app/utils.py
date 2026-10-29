import base64
import ipaddress
import os
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

TAG = "[wgpanel]"  # marca tudo que o painel cria no MikroTik

_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1, "ms": 0}
_DURATION = re.compile(r"(\d+)(ms|w|d|h|m|s)")
_CLOCK = re.compile(r"^(?:(\d+)w)?(?:(\d+)d)?(\d+):(\d+):(\d+)")


def parse_duration(value) -> int | None:
    """Converte durações do RouterOS ('1d2h3m4s', '1w2d03:04:05') em segundos."""
    if not value:
        return None
    text = str(value).strip()
    clock = _CLOCK.match(text)
    if clock:
        w, d, h, m, s = (int(x or 0) for x in clock.groups())
        return w * 604800 + d * 86400 + h * 3600 + m * 60 + s
    parts = _DURATION.findall(text)
    if not parts:
        return None
    return sum(int(n) * _UNITS[u] for n, u in parts)


def to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def generate_keypair() -> tuple[str, str]:
    key = X25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


def generate_psk() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def parse_ports(spec: str) -> list[tuple[int, int]]:
    """'80,443,27015-27030' -> [(80,80),(443,443),(27015,27030)]. Lança ValueError se inválido."""
    ranges = []
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        if not (1 <= start <= end <= 65535):
            raise ValueError(f"Porta inválida: {part}")
        ranges.append((start, end))
    if not ranges:
        raise ValueError("Informe ao menos uma porta")
    return ranges


def ports_overlap(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    return any(s1 <= e2 and s2 <= e1 for s1, e1 in a for s2, e2 in b)


def split_list(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,\n;\s]+", text or "") if x.strip()]


def validate_networks(text: str) -> list[str]:
    nets = []
    for item in split_list(text):
        nets.append(str(ipaddress.ip_network(item, strict=False)))
    return nets


def is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "nunca"
    parts = []
    for label, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            parts.append(f"{seconds // size}{label}")
            seconds %= size
    if not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts[:2])
