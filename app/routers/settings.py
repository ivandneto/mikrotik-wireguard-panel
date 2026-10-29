from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import config
from ..db import User, audit
from ..mikrotik import mt
from ..security import get_db, require_admin
from ..settings_store import get_settings, save_settings
from ..utils import validate_networks

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsIn(BaseModel):
    endpoint_remote: str = ""
    endpoint_local: str = ""
    wg_dns: str = ""
    wg_lan_networks: str = ""
    wg_keepalive: int = Field(25, ge=0, le=600)
    wg_mtu: int = Field(0, ge=0, le=9000)
    wg_store_keys_on_router: bool = True
    nat_match_mode: Literal["wan_list", "interface", "dst_local"] = "wan_list"
    nat_in_target: str = "WAN"
    wan_interface: str = ""
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
    try:
        nets = validate_networks(body.wg_lan_networks)
    except ValueError as exc:
        raise HTTPException(400, f"Rede LAN inválida: {exc}")
    data = body.model_dump()
    data["wg_lan_networks"] = ", ".join(nets)
    save_settings(db, data)
    audit(db, user.username, "configurações alteradas")
    return {"ok": True}


@router.get("/router-options")
async def router_options(_: User = Depends(require_admin)):
    """Interfaces e listas do MikroTik para preencher os campos de seleção."""
    interfaces = await mt.get("/interface")
    lists = await mt.get("/interface/list")
    return {
        "interfaces": [i["name"] for i in interfaces if i.get("dynamic") != "true"],
        "interface_lists": [l["name"] for l in lists],
    }
