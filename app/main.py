import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import config
from .db import SessionLocal, User, init_db
from .mikrotik import MikroTikError, mt
from .routers import alerts, auth, backup, monitor, nat, settings, users, wireguard
from .security import hash_password
from .services import collector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("wgpanel")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _ensure_admin():
    db = SessionLocal()
    try:
        if db.query(User).count():
            return
        password = config.ADMIN_PASSWORD or secrets.token_urlsafe(12)
        db.add(User(username=config.ADMIN_USER, password_hash=hash_password(password), role="admin"))
        db.commit()
        if config.ADMIN_PASSWORD:
            logger.warning("Usuário administrador '%s' criado", config.ADMIN_USER)
        else:
            logger.warning("=" * 60)
            logger.warning("Usuário administrador criado: %s / senha: %s", config.ADMIN_USER, password)
            logger.warning("Troque a senha após o primeiro login!")
            logger.warning("=" * 60)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    _ensure_admin()
    task = asyncio.create_task(collector.run_forever())
    yield
    task.cancel()
    await mt.close()


app = FastAPI(title="MikroTik WG Panel", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    session_cookie="wgpanel_session",
    max_age=config.SESSION_HOURS * 3600,
    same_site="strict",
    https_only=config.COOKIE_SECURE,
)


@app.exception_handler(MikroTikError)
async def mikrotik_error_handler(_request: Request, exc: MikroTikError):
    return JSONResponse(status_code=502, content={"detail": f"MikroTik: {exc}"})


for module in (auth, wireguard, nat, monitor, alerts, users, settings, backup):
    app.include_router(module.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True, "router_online": collector.state["router"]["online"]}
