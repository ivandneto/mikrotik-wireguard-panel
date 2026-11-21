import io
import time

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import User, audit
from ..i18n import tr
from ..security import current_user, decrypt, encrypt, get_db, hash_password, password_fingerprint, verify_password
from ..services import totp

router = APIRouter(prefix="/api/auth", tags=["auth"])

_failures: dict[str, list[float]] = {}

BAD_CODE = "Código inválido ou vencido. Confira o relógio do celular e tente o código novo."


class LoginIn(BaseModel):
    username: str
    password: str
    code: str | None = None  # 6-digit code from the app, or a recovery code, when two-step login is on


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class TwoFactorPasswordIn(BaseModel):
    password: str


class TwoFactorCodeIn(BaseModel):
    code: str


class TwoFactorConfirmIn(BaseModel):
    password: str
    code: str


def user_out(user: User) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role, "totp": bool(user.totp_enabled)}


def _secret(user: User) -> str:
    return decrypt(user.totp_secret_enc) if user.totp_secret_enc else ""


def check_second_factor(db: Session, user: User, code: str) -> str | None:
    """Accepts the app's 6-digit code or one recovery code (consumed). Returns "totp", "recovery" or None."""
    code = (code or "").strip()
    if totp.verify(_secret(user), code, user_id=user.id):
        return "totp"
    remaining = totp.use_recovery_code(user.recovery_codes_json, code)
    if remaining is not None:
        user.recovery_codes_json = remaining
        db.commit()
        return "recovery"
    return None


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    now = time.time()
    attempts = [t for t in _failures.get(ip, []) if now - t < 300]
    if len(attempts) >= 10:
        raise HTTPException(429, "Muitas tentativas de login. Aguarde 5 minutos.")
    user = db.query(User).filter(User.username == body.username.strip()).first()
    if not user or not verify_password(body.password, user.password_hash):
        attempts.append(now)
        _failures[ip] = attempts
        raise HTTPException(401, tr("Usuário ou senha inválidos"))
    how = ""
    if user.totp_enabled:
        if not body.code:
            return {"otp_required": True}  # password ok: the login screen now asks for the code (no session yet)
        method = check_second_factor(db, user, body.code)
        if not method:
            attempts.append(now)  # code guesses count towards the same 10-per-5-minutes limit
            _failures[ip] = attempts
            raise HTTPException(401, tr(BAD_CODE))
        if method == "recovery":
            how = " · " + tr("com código de recuperação ({left} restantes)", left=totp.recovery_left(user.recovery_codes_json))
    _failures.pop(ip, None)
    request.session.clear()
    request.session.update(uid=user.id, pv=password_fingerprint(user))
    audit(db, user.username, "login", f"IP {ip}{how}")
    return user_out(user)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


@router.post("/password")
def change_password(body: PasswordIn, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Senha atual incorreta")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    request.session["pv"] = password_fingerprint(user)
    audit(db, user.username, "senha alterada")
    return {"ok": True}


# ------------------------------------------------------------------ two-step login (TOTP)

@router.get("/2fa")
def two_factor_status(user: User = Depends(current_user)):
    return {"enabled": bool(user.totp_enabled), "recovery_left": totp.recovery_left(user.recovery_codes_json)}


@router.post("/2fa/setup")
def two_factor_setup(body: TwoFactorPasswordIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Step 1: creates a new secret (not active yet) and returns the QR Code for the authenticator app."""
    if user.totp_enabled:
        raise HTTPException(400, tr("A verificação em 2 etapas já está ativada"))
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(400, tr("Senha atual incorreta"))
    secret = totp.new_secret()
    user.totp_secret_enc = encrypt(secret)
    db.commit()
    uri = totp.otpauth_uri(secret, user.username)
    buf = io.BytesIO()
    qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2).save(buf)
    return {"secret": secret, "uri": uri, "qr_svg": buf.getvalue().decode()}


@router.post("/2fa/enable")
def two_factor_enable(body: TwoFactorCodeIn, request: Request, user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
    """Step 2: the first code from the app proves it was set up right; returns the recovery codes (shown once)."""
    if user.totp_enabled:
        raise HTTPException(400, tr("A verificação em 2 etapas já está ativada"))
    if not user.totp_secret_enc or not totp.verify(_secret(user), body.code, user_id=user.id):
        raise HTTPException(400, tr(BAD_CODE))
    codes, stored = totp.new_recovery_codes()
    user.totp_enabled = True
    user.recovery_codes_json = stored
    db.commit()
    request.session["pv"] = password_fingerprint(user)  # keep this session; other sessions are logged out
    audit(db, user.username, "verificação em 2 etapas ativada")
    return {"enabled": True, "recovery_codes": codes}


def _confirm(db: Session, user: User, body: TwoFactorConfirmIn):
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(400, tr("Senha atual incorreta"))
    if not user.totp_enabled:
        raise HTTPException(400, tr("A verificação em 2 etapas não está ativada"))
    if not check_second_factor(db, user, body.code):
        raise HTTPException(400, tr(BAD_CODE))


@router.post("/2fa/disable")
def two_factor_disable(body: TwoFactorConfirmIn, request: Request, user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    _confirm(db, user, body)
    user.totp_enabled = False
    user.totp_secret_enc = ""
    user.recovery_codes_json = "[]"
    db.commit()
    request.session["pv"] = password_fingerprint(user)
    audit(db, user.username, "verificação em 2 etapas desativada")
    return {"enabled": False}


@router.post("/2fa/recovery")
def two_factor_new_recovery(body: TwoFactorConfirmIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """New set of recovery codes; the old ones stop working."""
    _confirm(db, user, body)
    codes, stored = totp.new_recovery_codes()
    user.recovery_codes_json = stored
    db.commit()
    audit(db, user.username, "novos códigos de recuperação gerados")
    return {"recovery_codes": codes}
