from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import User, audit
from ..i18n import tr
from ..security import get_db, require_admin
from ..services.alerts import EVENT_LABELS, dispatch
from ..settings_store import get_settings, save_settings

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class AlertsIn(BaseModel):
    telegram_enabled: bool = False
    telegram_token: str = ""
    telegram_chat_id: str = ""
    discord_enabled: bool = False
    discord_webhook: str = ""
    events: dict[str, bool] = {}


@router.get("")
def get_alerts(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return {"config": get_settings(db)["alerts"], "labels": EVENT_LABELS}


@router.put("")
def save_alerts(body: AlertsIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if body.discord_webhook and not body.discord_webhook.startswith("https://"):
        raise HTTPException(400, tr("O webhook do Discord deve começar com https://"))
    data = body.model_dump()
    data["events"] = {k: v for k, v in body.events.items() if k in EVENT_LABELS}
    save_settings(db, {"alerts": data})
    audit(db, user.username, "alertas configurados")
    return {"ok": True}


@router.post("/test")
async def test_alerts(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    settings = get_settings(db)
    cfg, lang = settings["alerts"], settings.get("language")
    results = await dispatch(cfg, tr("Teste de alerta", lang=lang),
                             tr("Mensagem de teste enviada por {user}. Está funcionando! 🎉", lang=lang, user=user.username),
                             "info")
    if not results:
        raise HTTPException(400, tr("Nenhum canal ativo. Ative e salve o Telegram e/ou o Discord primeiro."))
    return {"results": results}
