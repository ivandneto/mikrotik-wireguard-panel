"""Two-step login: time-based one-time codes (TOTP, RFC 6238 — Google Authenticator, Authy, Microsoft
Authenticator…) and single-use recovery codes. Standard library only."""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD = 30
WINDOW = 1  # accept the previous and next 30-second code too (clock drift on the phone)
RECOVERY_COUNT = 8
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i look-alikes
_last_step: dict[tuple[int, str], int] = {}  # (user id, secret) → last accepted time step (a code can't be used twice)


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def code_now(secret: str, at: float | None = None) -> str:
    return _code_at(secret, int((at if at is not None else time.time()) // PERIOD))


def verify(secret: str, code: str, user_id: int | None = None, at: float | None = None) -> bool:
    """True when the 6-digit code is valid now (±30 s). With user_id, a code that was already accepted is refused."""
    code = "".join(c for c in (code or "") if c.isdigit())
    if len(code) != DIGITS or not secret:
        return False
    step = int((at if at is not None else time.time()) // PERIOD)
    for candidate in range(step - WINDOW, step + WINDOW + 1):
        if hmac.compare_digest(_code_at(secret, candidate), code):
            if user_id is not None:
                key = (user_id, secret)  # a new secret (set up again) starts fresh
                if _last_step.get(key, -1) >= candidate:
                    return False  # replay of a code already used
                for old in [k for k in _last_step if k[0] == user_id and k != key]:
                    del _last_step[old]
                _last_step[key] = candidate
            return True
    return False


def otpauth_uri(secret: str, username: str, issuer: str = "WG Panel") -> str:
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={PERIOD}"


# ------------------------------------------------------------------ recovery codes

def _hash_recovery(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace("-", "").replace(" ", "").encode()).hexdigest()


def new_recovery_codes() -> tuple[list[str], str]:
    """Returns the codes to show once and the JSON with their hashes to store."""
    codes = []
    for _ in range(RECOVERY_COUNT):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes, json.dumps([_hash_recovery(c) for c in codes])


def use_recovery_code(stored_json: str, code: str) -> str | None:
    """If the code is one of the stored ones, returns the JSON without it (it can't be used again); else None."""
    hashes = json.loads(stored_json or "[]")
    h = _hash_recovery(code)
    for i, stored in enumerate(hashes):
        if hmac.compare_digest(stored, h):
            return json.dumps(hashes[:i] + hashes[i + 1:])
    return None


def recovery_left(stored_json: str) -> int:
    return len(json.loads(stored_json or "[]"))
