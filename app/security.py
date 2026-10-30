import base64
import hashlib
import hmac
import os

from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .config import config
from .db import SessionLocal, User
from .i18n import tr

_PBKDF2_ROUNDS = 310_000
_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(config.SECRET_KEY.encode()).digest()))


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ROUNDS)
    return hmac.compare_digest(check.hex(), digest)


def password_fingerprint(user: User) -> str:
    """Vai na sessão: trocar a senha invalida as sessões antigas."""
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet.decrypt(value.encode()).decode()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if not user or request.session.get("pv") != password_fingerprint(user):
        request.session.clear()
        raise HTTPException(401, tr("Não autenticado"))
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Apenas administradores podem fazer isso")
    return user
