from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import config
from ..db import Router, User, audit
from ..routerctx import use_router
from ..mikrotik import mt
from ..security import get_db, require_admin
from ..settings_store import get_settings, save_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsIn(BaseModel):
    wg_keepalive: int = Field(25, ge=0, le=600)
    wg_mtu: int = Field(0, ge=0, le=9000)
    wg_store_keys_on_router: bool = True
    online_threshold: int = Field(180, ge=30, le=3600)
    retention_days: int = Field(30, ge=1, le=365)


@router.get("")
def read_settings(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings(db)
    s.pop("alerts", None)
    s["mikrotik_url"] = config.MIKROTIK_URL
    s["mikrotik_user"] = config.MIKROTIK_USER
    s["poll_interval"] = config.POLL_INTERVAL
    return s


@router.put("")
def write_settings(body: SettingsIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    save_settings(db, body.model_dump())
    audit(db, user.username, "configurações alteradas")
    return {"ok": True}


@router.get("/router-options")
async def router_options(_: User = Depends(require_admin), rt: Router = Depends(use_router)):
    """Interfaces e listas do MikroTik para preencher os campos de seleção."""
    interfaces = await mt.get("/interface")
    lists = await mt.get("/interface/list")
    return {
        "interfaces": [i["name"] for i in interfaces if i.get("dynamic") != "true"],
        "interface_lists": [l["name"] for l in lists],
    }
