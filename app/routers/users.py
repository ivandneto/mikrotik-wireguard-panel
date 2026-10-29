from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import User, audit, iso
from ..security import get_db, hash_password, require_admin

router = APIRouter(prefix="/api/users", tags=["users"])


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=8)
    role: Literal["admin", "viewer"] = "viewer"


class UserUpdate(BaseModel):
    password: str | None = Field(None, min_length=8)
    role: Literal["admin", "viewer"] | None = None


def _out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role, "created_at": iso(u.created_at)}


def _ensure_other_admin(db: Session, user: User):
    if user.role == "admin" and db.query(User).filter(User.role == "admin", User.id != user.id).count() == 0:
        raise HTTPException(400, "Precisa existir pelo menos um administrador")


@router.get("")
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_out(u) for u in db.query(User).order_by(User.username).all()]


@router.post("")
def create_user(body: UserIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, "Já existe um usuário com esse nome")
    user = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    db.commit()
    audit(db, admin.username, "usuário criado", f"{user.username} ({user.role})")
    return _out(user)


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Usuário não encontrado")
    if body.role and body.role != user.role:
        if body.role == "viewer":
            _ensure_other_admin(db, user)
        user.role = body.role
    if body.password:
        user.password_hash = hash_password(body.password)
    db.commit()
    audit(db, admin.username, "usuário alterado", user.username)
    return _out(user)


@router.delete("/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Usuário não encontrado")
    if user.id == admin.id:
        raise HTTPException(400, "Você não pode remover o próprio usuário")
    _ensure_other_admin(db, user)
    db.delete(user)
    db.commit()
    audit(db, admin.username, "usuário removido", user.username)
    return {"ok": True}
