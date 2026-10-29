import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger("wgpanel")


def _bool(value, default=False):
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "sim")


class Config:
    def __init__(self):
        self.DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Conexão com o MikroTik (API REST do RouterOS v7)
        self.MIKROTIK_URL = os.getenv("MIKROTIK_URL", "http://192.168.88.1").rstrip("/")
        self.MIKROTIK_USER = os.getenv("MIKROTIK_USER", "wgpanel")
        self.MIKROTIK_PASSWORD = os.getenv("MIKROTIK_PASSWORD", "")
        self.MIKROTIK_VERIFY_TLS = _bool(os.getenv("MIKROTIK_VERIFY_TLS"), False)
        self.MIKROTIK_TIMEOUT = float(os.getenv("MIKROTIK_TIMEOUT", "10"))

        # Painel
        self.ADMIN_USER = os.getenv("ADMIN_USER", "admin")
        self.ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
        self.POLL_INTERVAL = max(10, int(os.getenv("POLL_INTERVAL", "30")))
        self.SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
        self.COOKIE_SECURE = _bool(os.getenv("COOKIE_SECURE"), False)
        self.SECRET_KEY = os.getenv("SECRET_KEY") or self._load_or_create_secret()

    def _load_or_create_secret(self) -> str:
        """Gera uma chave na primeira execução e guarda em DATA_DIR para sobreviver a reinícios."""
        path = self.DATA_DIR / "secret.key"
        if path.exists():
            return path.read_text().strip()
        key = secrets.token_urlsafe(48)
        path.write_text(key)
        logger.warning("SECRET_KEY não definida: chave gerada em %s (faça backup dela!)", path)
        return key


config = Config()
