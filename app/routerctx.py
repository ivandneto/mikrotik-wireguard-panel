"""Qual MikroTik a requisição está usando: o escolhido no seletor do painel (cabeçalho X-Router-Id)."""
import contextvars

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import Router
from .mikrotik import client_for, use
from .security import get_db

_router: contextvars.ContextVar[Router | None] = contextvars.ContextVar("router", default=None)


def main_router(db: Session) -> Router:
    router = db.query(Router).filter(Router.is_main.is_(True)).first()
    if router is None:
        raise HTTPException(500, "Nenhum roteador principal configurado")
    return router


async def use_router(request: Request, db: Session = Depends(get_db)) -> Router:
    raw = request.headers.get("x-router-id") or request.query_params.get("router") or ""
    router = db.get(Router, int(raw)) if raw.isdigit() else None
    if router is None:
        router = main_router(db)
    _router.set(router)
    use(client_for(router))
    return router


def current_router() -> Router:
    router = _router.get()
    if router is None:
        raise RuntimeError("use_router não foi aplicado a esta rota")
    return router
