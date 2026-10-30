"""Ferramentas do roteador selecionado: ping, traceroute, checagem de segurança, backups automáticos e
atualização do RouterOS."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import Router, User, audit
from ..i18n import tr
from ..mikrotik import MikroTikError, mt
from ..routerctx import use_router
from ..security import get_db, require_admin
from ..services import auto_tasks, router_tools
from ..settings_store import get_settings

router = APIRouter(prefix="/api/tools", tags=["tools"], dependencies=[Depends(use_router)])


class PingIn(BaseModel):
    address: str = Field(min_length=1, max_length=253)
    count: int = Field(4, ge=1, le=20)


class TraceIn(BaseModel):
    address: str = Field(min_length=1, max_length=253)


def _host(address: str) -> str:
    address = address.strip()
    if not router_tools.HOST_RE.match(address):
        raise HTTPException(400, tr("Endereço inválido: use um IP ou um nome (ex.: 8.8.8.8, google.com)"))
    return address


def _mikrotik_error(exc: MikroTikError):
    detail = router_tools.permission_error() if router_tools.needs_permission(exc) else str(exc)
    return HTTPException(502, detail)


@router.post("/ping")
async def ping(body: PingIn, _: User = Depends(require_admin)):
    try:
        return await router_tools.ping(mt, _host(body.address), body.count)
    except MikroTikError as exc:
        raise _mikrotik_error(exc)


@router.post("/traceroute")
async def traceroute(body: TraceIn, _: User = Depends(require_admin)):
    try:
        return {"hops": await router_tools.traceroute(mt, _host(body.address))}
    except MikroTikError as exc:
        raise _mikrotik_error(exc)


@router.get("/security")
async def security(request: Request, _: User = Depends(require_admin), rt: Router = Depends(use_router)):
    ports = {"8080", str(request.url.port or "")} - {""}
    update = auto_tasks.router_state(rt.id).get("update")
    findings = await router_tools.security_check(mt, request.client.host if request.client else "", ports, update)
    return {"findings": findings, "update": update}


# ------------------------------------------------------------------ atualização do RouterOS

@router.post("/update/check")
async def check_update(_: User = Depends(require_admin), rt: Router = Depends(use_router)):
    try:
        info = await router_tools.check_update(mt)
    except MikroTikError as exc:
        raise _mikrotik_error(exc)
    auto_tasks._update_state(rt.id, update=info, update_checked=auto_tasks._now().isoformat())
    return info


# ------------------------------------------------------------------ backups automáticos

@router.get("/backups")
def list_backups(_: User = Depends(require_admin), rt: Router = Depends(use_router), db: Session = Depends(get_db)):
    st = auto_tasks.router_state(rt.id)
    s = get_settings(db)
    return {
        "backups": auto_tasks.list_backups(rt.id),
        "last_check": st.get("backup_checked"),
        "error": st.get("backup_error"),
        "settings": {k: s[k] for k in ("auto_backup_enabled", "auto_backup_interval", "auto_backup_keep")},
    }


@router.post("/backups/run")
async def run_backup(user: User = Depends(require_admin), rt: Router = Depends(use_router),
                     db: Session = Depends(get_db)):
    try:
        res = await auto_tasks.run_backup(rt, int(get_settings(db).get("auto_backup_keep") or 14))
    except MikroTikError as exc:
        err = _mikrotik_error(exc)
        auto_tasks._update_state(rt.id, backup_error=err.detail)
        raise err
    if res["saved"]:
        audit(db, user.username, "backup automático salvo", f"{rt.name}: {res['name']}")
    return res


@router.get("/backups/{name}")
def download_backup(name: str, _: User = Depends(require_admin), rt: Router = Depends(use_router)):
    path = auto_tasks.backup_path(rt.id, name)
    if not path:
        raise HTTPException(404, tr("Backup não encontrado"))
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in rt.name)
    return PlainTextResponse(path.read_text("utf-8"), media_type="text/plain; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{safe}-{name}"'})


@router.get("/backups/{name}/diff")
def backup_diff(name: str, _: User = Depends(require_admin), rt: Router = Depends(use_router)):
    if not auto_tasks.backup_path(rt.id, name):
        raise HTTPException(404, tr("Backup não encontrado"))
    return PlainTextResponse(auto_tasks.diff_with_previous(rt.id, name) or tr("(sem diferenças)"))
