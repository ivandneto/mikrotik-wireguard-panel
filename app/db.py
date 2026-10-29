import json
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import config

engine = create_engine(
    f"sqlite:///{config.DATA_DIR / 'wgpanel.db'}",
    connect_args={"check_same_thread": False, "timeout": 15},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(conn, _record):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def utcnow() -> datetime:
    """Datas são guardadas em UTC sem fuso (o SQLite não armazena fuso)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # admin | viewer
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Peer(Base):
    """Cliente WireGuard criado pelo painel."""
    __tablename__ = "peers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(64), default="")
    ip: Mapped[str] = mapped_column(String(45), unique=True)
    public_key: Mapped[str] = mapped_column(String(64), unique=True)
    private_key_enc: Mapped[str] = mapped_column(Text)
    preshared_key_enc: Mapped[str] = mapped_column(Text)
    tunnel_mode: Mapped[str] = mapped_column(String(8), default="split")  # split | full
    mikrotik_id: Mapped[str] = mapped_column(String(16), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="")


class NatRule(Base):
    """Liberação de porta (dst-nat) criada pelo painel. TCP+UDP gera duas regras no MikroTik."""
    __tablename__ = "nat_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    template: Mapped[str] = mapped_column(String(64), default="")
    protocol: Mapped[str] = mapped_column(String(8))  # tcp | udp | both
    external_port: Mapped[str] = mapped_column(String(64))
    internal_ip: Mapped[str] = mapped_column(String(45))
    internal_port: Mapped[str] = mapped_column(String(64), default="")
    whitelist: Mapped[str] = mapped_column(Text, default="")
    health_check: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expired: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mikrotik_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="")

    @property
    def mikrotik_ids(self) -> list[str]:
        return json.loads(self.mikrotik_ids_json or "[]")

    @mikrotik_ids.setter
    def mikrotik_ids(self, value: list[str]):
        self.mikrotik_ids_json = json.dumps(value)


class PeerSample(Base):
    __tablename__ = "peer_samples"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    peer_id: Mapped[int] = mapped_column(Integer)
    rx: Mapped[int] = mapped_column(BigInteger)
    tx: Mapped[int] = mapped_column(BigInteger)
    online: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (Index("ix_peer_samples_peer_ts", "peer_id", "ts"),)


class NatSample(Base):
    __tablename__ = "nat_samples"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    rule_id: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(BigInteger)
    packets: Mapped[int] = mapped_column(BigInteger)
    healthy: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    __table_args__ = (Index("ix_nat_samples_rule_ts", "rule_id", "ts"),)


class SystemSample(Base):
    __tablename__ = "system_samples"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    cpu: Mapped[float] = mapped_column(Float)
    mem_pct: Mapped[float] = mapped_column(Float)
    wan_rx_bps: Mapped[float] = mapped_column(Float, default=0)
    wan_tx_bps: Mapped[float] = mapped_column(Float, default=0)


class Event(Base):
    """Eventos do sistema (conexões, quedas, expirações) — o que também vira alerta."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(8))  # info | success | warn | error
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)


class AuditLog(Base):
    """Ações feitas por usuários no painel."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")


def init_db():
    Base.metadata.create_all(engine)


def audit(db, username: str, action: str, detail: str = ""):
    db.add(AuditLog(username=username, action=action, detail=detail))
    db.commit()


def log_event(db, level: str, kind: str, message: str):
    db.add(Event(level=level, kind=kind, message=message))
    db.commit()
