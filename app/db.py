import os
import time
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

SCHEMA_VERSION = 2

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./swing.db")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Settings(Base):
    """Single-row table holding the risk rules of the strategy."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    currency: Mapped[str] = mapped_column(String(8), default="₹")
    account_size: Mapped[float] = mapped_column(Float, default=100000.0)  # starting capital
    risk_pct: Mapped[float] = mapped_column(Float, default=1.0)  # % of account risked per trade
    target1_rr: Mapped[float] = mapped_column(Float, default=2.0)  # minimum reward:risk
    target2_rr: Mapped[float] = mapped_column(Float, default=3.0)
    breakeven_r: Mapped[float] = mapped_column(Float, default=1.0)  # move stop to entry after +1R
    atr_multiple: Mapped[float] = mapped_column(Float, default=2.0)  # stop / trailing distance in ATRs
    default_stop_pct: Mapped[float] = mapped_column(Float, default=5.0)  # fallback when no ATR data
    max_hold_days: Mapped[int] = mapped_column(Integer, default=30)  # time stop (calendar days)
    max_open_positions: Mapped[int] = mapped_column(Integer, default=5)
    # appended to symbols without an exchange suffix, e.g. RELIANCE -> RELIANCE.NS (NSE India)
    exchange_suffix: Mapped[str] = mapped_column(String(8), default=".NS")
    schema_version: Mapped[int] = mapped_column(Integer, default=SCHEMA_VERSION)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    entry_date: Mapped[date] = mapped_column(Date)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float] = mapped_column(Float)  # initial stop, defines 1R
    stop_method: Mapped[str] = mapped_column(String(32), default="manual")
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # your own profit target
    shares: Mapped[int] = mapped_column(Integer)
    setup: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # override when no live data
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PriceCache(Base):
    """Daily OHLC history per symbol, so we don't hit the data provider on every page load."""

    __tablename__ = "price_cache"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    # list of {"d": "YYYY-MM-DD", "h": float, "l": float, "c": float}
    bars: Mapped[list] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


def _add_missing_columns() -> None:
    """create_all() doesn't alter existing tables, so add columns introduced in later versions."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"
                default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                if default is not None:
                    # existing rows of an upgraded DB start at version 1 so the data migration below runs
                    value = 1 if col.name == "schema_version" else default
                    ddl += f" DEFAULT {value!r}" if isinstance(value, str) else f" DEFAULT {value}"
                conn.execute(text(ddl))


def _migrate_data(s) -> None:
    settings = s.get(Settings, 1)
    if (settings.schema_version or 1) < 2:
        # v2: switched the dashboard from euro to Indian rupees
        if settings.currency == "€":
            settings.currency = "₹"
        settings.schema_version = 2
        s.commit()


def init_db(retries: int = 30) -> None:
    for attempt in range(retries):
        try:
            Base.metadata.create_all(engine)
            break
        except OperationalError:
            if attempt == retries - 1:
                raise
            time.sleep(2)
    _add_missing_columns()
    with SessionLocal() as s:
        if s.get(Settings, 1) is None:
            s.add(Settings(id=1))
            s.commit()
        _migrate_data(s)
