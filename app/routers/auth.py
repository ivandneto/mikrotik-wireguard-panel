import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import User, audit
from ..i18n import tr
from ..security import current_user, get_db, hash_password, password_fingerprint, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

_failures: dict[str, list[float]] = {}


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


def user_out(user: User) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role}


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
    _failures.pop(ip, None)
    request.session.clear()
    request.session.update(uid=user.id, pv=password_fingerprint(user))
    audit(db, user.username, "login", f"IP {ip}")
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
