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


class WgInterface(Base):
    """Interface WireGuard do MikroTik gerenciada pelo painel (ex.: wg-casa, wg-clientes)."""
    __tablename__ = "wg_interfaces"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    listen_port: Mapped[int] = mapped_column(Integer)
    address: Mapped[str] = mapped_column(String(64))  # IP do MikroTik na VPN + faixa (ex.: 10.10.0.1/24)
    public_key: Mapped[str] = mapped_column(String(64), default="")
    dns: Mapped[str] = mapped_column(String(128), default="")
    lan_networks: Mapped[str] = mapped_column(Text, default="")  # redes daqui alcançáveis pelos peers
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Peer(Base):
    """Cliente WireGuard criado pelo painel: um dispositivo ou outro MikroTik (site)."""
    __tablename__ = "peers"
    id: Mapped[int] = mapped_column(primary_key=True)
    interface_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(8), default="device")  # device | site
    remote_networks: Mapped[str] = mapped_column(Text, default="")  # site: LANs atrás do MikroTik remoto
    # site: one_way = só você acessa a rede do cliente; two_way = os dois lados se acessam
    site_access: Mapped[str] = mapped_column(String(8), default="one_way")
    route_ids_json: Mapped[str] = mapped_column(Text, default="[]")  # site: rotas criadas no MikroTik
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

    @property
    def route_ids(self) -> list[str]:
        return json.loads(self.route_ids_json or "[]")

    @route_ids.setter
    def route_ids(self, value: list[str]):
        self.route_ids_json = json.dumps(value)


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
    _migrate()


def _migrate():
    """Atualiza bancos de versões anteriores (create_all não adiciona colunas em tabelas existentes)."""
    new_peer_columns = {
        "interface_id": "INTEGER",
        "kind": "VARCHAR(8) NOT NULL DEFAULT 'device'",
        "remote_networks": "TEXT NOT NULL DEFAULT ''",
        "site_access": "VARCHAR(8) NOT NULL DEFAULT 'one_way'",
        "route_ids_json": "TEXT NOT NULL DEFAULT '[]'",
    }
    with engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(peers)")}
        for column, ddl in new_peer_columns.items():
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE peers ADD COLUMN {column} {ddl}")

    # versões antigas tinham uma única interface guardada nas configurações
    db = SessionLocal()
    try:
        if db.query(WgInterface).count():
            return
        legacy = {row.key: json.loads(row.value) for row in db.query(Setting).all()}
        if not legacy.get("wg_configured"):
            return
        iface = WgInterface(
            name=legacy.get("wg_interface", "wg-panel"),
            listen_port=int(legacy.get("wg_listen_port", 13231)),
            address=legacy.get("wg_address", "10.10.0.1/24"),
            public_key=legacy.get("wg_server_public_key", ""),
            dns=legacy.get("wg_dns", ""),
            lan_networks=legacy.get("wg_lan_networks", ""),
            description="Dispositivos",
        )
        db.add(iface)
        db.flush()
        db.query(Peer).filter(Peer.interface_id.is_(None)).update({Peer.interface_id: iface.id})
        db.commit()
    finally:
        db.close()


def audit(db, username: str, action: str, detail: str = ""):
    db.add(AuditLog(username=username, action=action, detail=detail))
    db.commit()


def log_event(db, level: str, kind: str, message: str):
    db.add(Event(level=level, kind=kind, message=message))
    db.commit()
