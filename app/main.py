import csv
import io
import logging
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

import calc
import market
from db import SessionLocal, Settings, Trade, init_db

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Swing Trading Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


def get_db():
    with SessionLocal() as s:
        yield s


def get_settings(db: Session) -> Settings:
    return db.get(Settings, 1)


# ---------------------------------------------------------------- schemas


class SettingsIn(BaseModel):
    currency: str = Field(max_length=8)
    account_size: float = Field(gt=0)
    risk_pct: float = Field(gt=0, le=100)
    target1_rr: float = Field(gt=0)
    target2_rr: float = Field(gt=0)
    breakeven_r: float = Field(gt=0)
    atr_multiple: float = Field(gt=0)
    default_stop_pct: float = Field(gt=0, lt=100)
    max_hold_days: int = Field(gt=0)
    max_open_positions: int = Field(gt=0)
    exchange_suffix: str = Field(default="", max_length=8)

    @field_validator("exchange_suffix")
    @classmethod
    def suffix(cls, v: str) -> str:
        v = v.strip().upper()
        return f".{v}" if v and not v.startswith(".") else v


class TradeIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    entry_date: date
    entry_price: float = Field(gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    shares: int | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    setup: str | None = None
    notes: str | None = None

    @field_validator("symbol")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.strip().upper()


class TradeUpdate(BaseModel):
    stop_price: float | None = Field(default=None, gt=0)
    shares: int | None = Field(default=None, gt=0)
    manual_price: float | None = None  # send 0 to clear
    target_price: float | None = None  # send 0 to clear
    setup: str | None = None
    notes: str | None = None


class CloseIn(BaseModel):
    exit_price: float = Field(gt=0)
    exit_date: date
    exit_reason: str | None = None


# ---------------------------------------------------------------- helpers


def normalize_symbol(symbol: str, settings: Settings) -> str:
    """RELIANCE -> RELIANCE.NS when an exchange suffix is configured; leaves AAPL.US-style symbols, indices (^NSEI) alone."""
    suffix = settings.exchange_suffix or ""
    if suffix and "." not in symbol and not symbol.startswith("^") and "=" not in symbol:
        return symbol + suffix
    return symbol


def capital_state(db: Session, settings: Settings, exclude_id: int | None = None) -> dict:
    """Equity = starting capital + realised P&L; available = equity - cost of open positions."""
    realised = sum(
        calc.closed_result(t)["pnl"] for t in db.scalars(select(Trade).where(Trade.status == "closed"))
    )
    deployed = sum(
        t.entry_price * t.shares
        for t in db.scalars(select(Trade).where(Trade.status == "open"))
        if t.id != exclude_id
    )
    return calc.capital(settings, realised, deployed)


def resolve_plan(db: Session, data: TradeIn, settings: Settings, fetch: bool = True) -> dict:
    data.symbol = normalize_symbol(data.symbol, settings)
    if data.target_price is not None and data.target_price <= data.entry_price:
        raise HTTPException(400, "Target price must be above the entry price.")
    cap = capital_state(db, settings)
    ind, data_error = {}, None
    if fetch:
        cache = market.get_bars(db, data.symbol, data.entry_date)
        data_error = cache.error
        ind = market.indicators(cache.bars, entry_date=data.entry_date, as_of=data.entry_date)
    if data.stop_price is not None:
        stop, method = data.stop_price, "manual"
    else:
        stop, method = calc.initial_stop(data.entry_price, settings, ind.get("atr14"))
    if stop >= data.entry_price:
        raise HTTPException(400, "Stop must be below the entry price (long trades only).")
    suggested = calc.position_size(data.entry_price, stop, settings, cap["equity"], cap["available_capital"])
    shares = data.shares or suggested
    return {
        "symbol": data.symbol,
        **cap,
        "available_after": cap["available_capital"] - data.entry_price * shares,
        "stop_price": stop,
        "stop_method": method,
        "shares": shares,
        "suggested_shares": suggested,
        "atr_at_entry": ind.get("atr14"),
        "trend_at_entry": calc.trend_check(ind),
        "data_error": data_error,
        **calc.plan(data.entry_price, stop, shares, settings, data.target_price, cap["equity"]),
    }


def trade_dict(t: Trade) -> dict:
    return {
        "id": t.id,
        "symbol": t.symbol,
        "entry_date": t.entry_date.isoformat(),
        "entry_price": t.entry_price,
        "stop_price": t.stop_price,
        "stop_method": t.stop_method,
        "target_price": t.target_price,
        "shares": t.shares,
        "setup": t.setup,
        "notes": t.notes,
        "manual_price": t.manual_price,
        "status": t.status,
        "exit_date": t.exit_date.isoformat() if t.exit_date else None,
        "exit_price": t.exit_price,
        "exit_reason": t.exit_reason,
    }


def enrich_open(db: Session, t: Trade, settings: Settings, force: bool = False,
                equity: float | None = None) -> dict:
    cache = market.get_bars(db, t.symbol, t.entry_date, force=force)
    ind = market.indicators(cache.bars, entry_date=t.entry_date)
    if equity is None:
        equity = capital_state(db, settings)["equity"]
    return {**trade_dict(t), **calc.evaluate(t, settings, ind, equity=equity), "data_error": cache.error}


def get_trade(db: Session, trade_id: int) -> Trade:
    t = db.get(Trade, trade_id)
    if t is None:
        raise HTTPException(404, "Trade not found")
    return t


# ---------------------------------------------------------------- routes


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/settings")
def read_settings(db: Session = Depends(get_db)):
    s = get_settings(db)
    return {k: getattr(s, k) for k in SettingsIn.model_fields}


@app.put("/api/settings")
def write_settings(body: SettingsIn, db: Session = Depends(get_db)):
    s = get_settings(db)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    db.commit()
    return read_settings(db)


@app.post("/api/preview")
def preview(body: TradeIn, db: Session = Depends(get_db)):
    """Calculate the plan for a trade without saving it."""
    return resolve_plan(db, body, get_settings(db))


@app.post("/api/trades")
def create_trade(body: TradeIn, db: Session = Depends(get_db)):
    settings = get_settings(db)
    p = resolve_plan(db, body, settings)
    if p["shares"] <= 0:
        raise HTTPException(400, "Position size works out to 0 shares — check account size / risk % / stop.")
    t = Trade(
        symbol=body.symbol,
        entry_date=body.entry_date,
        entry_price=body.entry_price,
        stop_price=p["stop_price"],
        stop_method=p["stop_method"],
        target_price=body.target_price,
        shares=p["shares"],
        setup=body.setup or None,
        notes=body.notes or None,
    )
    db.add(t)
    db.commit()
    return enrich_open(db, t, settings)


@app.get("/api/trades")
def list_trades(status: str = "open", refresh: bool = False, db: Session = Depends(get_db)):
    settings = get_settings(db)
    q = select(Trade)
    if status != "all":
        q = q.where(Trade.status == status)
    trades = db.scalars(q.order_by(Trade.entry_date.desc(), Trade.id.desc())).all()
    out = []
    refreshed: set[str] = set()
    equity = capital_state(db, settings)["equity"]
    for t in trades:
        if t.status == "open":
            force = refresh and t.symbol not in refreshed
            refreshed.add(t.symbol)
            out.append(enrich_open(db, t, settings, force=force, equity=equity))
        else:
            out.append({**trade_dict(t), **calc.closed_result(t)})
    return out


@app.put("/api/trades/{trade_id}")
def update_trade(trade_id: int, body: TradeUpdate, db: Session = Depends(get_db)):
    t = get_trade(db, trade_id)
    data = body.model_dump(exclude_unset=True)
    if "stop_price" in data and data["stop_price"] is not None:
        if data["stop_price"] >= t.entry_price:
            raise HTTPException(400, "Initial stop must be below the entry price.")
        t.stop_price, t.stop_method = data["stop_price"], "manual"
    if data.get("shares"):
        t.shares = data["shares"]
    if "manual_price" in data:
        t.manual_price = data["manual_price"] or None
    if "target_price" in data:
        if data["target_price"] and data["target_price"] <= t.entry_price:
            raise HTTPException(400, "Target price must be above the entry price.")
        t.target_price = data["target_price"] or None
    for k in ("setup", "notes"):
        if k in data:
            setattr(t, k, data[k] or None)
    db.commit()
    settings = get_settings(db)
    return enrich_open(db, t, settings) if t.status == "open" else {**trade_dict(t), **calc.closed_result(t)}


@app.post("/api/trades/{trade_id}/close")
def close_trade(trade_id: int, body: CloseIn, db: Session = Depends(get_db)):
    t = get_trade(db, trade_id)
    if t.status != "open":
        raise HTTPException(400, "Trade is already closed")
    if body.exit_date < t.entry_date:
        raise HTTPException(400, "Exit date is before entry date")
    t.status, t.exit_price, t.exit_date, t.exit_reason = "closed", body.exit_price, body.exit_date, body.exit_reason
    t.manual_price = None
    db.commit()
    return {**trade_dict(t), **calc.closed_result(t)}


@app.post("/api/trades/{trade_id}/reopen")
def reopen_trade(trade_id: int, db: Session = Depends(get_db)):
    t = get_trade(db, trade_id)
    t.status, t.exit_price, t.exit_date, t.exit_reason = "open", None, None, None
    db.commit()
    return enrich_open(db, t, get_settings(db))


@app.delete("/api/trades/{trade_id}")
def delete_trade(trade_id: int, db: Session = Depends(get_db)):
    db.delete(get_trade(db, trade_id))
    db.commit()
    return {"ok": True}


@app.get("/api/summary")
def summary(db: Session = Depends(get_db)):
    settings = get_settings(db)
    cap = capital_state(db, settings)
    open_trades = [
        enrich_open(db, t, settings, equity=cap["equity"])
        for t in db.scalars(select(Trade).where(Trade.status == "open"))
    ]
    closed = db.scalars(
        select(Trade).where(Trade.status == "closed").order_by(Trade.exit_date, Trade.id)
    ).all()
    stats = calc.journal_stats([calc.closed_result(t) for t in closed])
    unrealized = sum(t["pnl"] or 0 for t in open_trades)
    # positions without a price are valued at cost
    market_value = sum(t["market_value"] if t["market_value"] is not None else t["position_value"] for t in open_trades)
    return {
        **cap,
        "open_positions": len(open_trades),
        "max_open_positions": settings.max_open_positions,
        "capital_deployed_pct": cap["capital_deployed"] / cap["equity"] * 100 if cap["equity"] else None,
        "market_value": market_value,
        "account_value": cap["available_capital"] + market_value,  # cash + open positions at today's price
        "open_risk": sum(t["open_risk"] or 0 for t in open_trades),
        "unrealized_pnl": unrealized,
        "potential_target1": sum(t["profit_target1"] for t in open_trades),
        "potential_target2": sum(t["profit_target2"] for t in open_trades),
        "potential_plan": sum(t["profit_plan"] for t in open_trades),
        "max_loss_total": sum(t["max_loss"] for t in open_trades),
        "exit_signals": sum(1 for t in open_trades if t["signal"] == calc.EXIT),
        "action_signals": sum(1 for t in open_trades if t["signal"] == calc.ACTION),
        "account_size": settings.account_size,
        **stats,
    }


@app.get("/api/export.csv")
def export_csv(db: Session = Depends(get_db)):
    trades = db.scalars(select(Trade).order_by(Trade.entry_date, Trade.id)).all()
    buf = io.StringIO()
    cols = list(trade_dict(trades[0]).keys()) if trades else ["id"]
    w = csv.DictWriter(buf, fieldnames=cols + ["pnl", "r_multiple"])
    w.writeheader()
    for t in trades:
        row = trade_dict(t)
        if t.status == "closed":
            res = calc.closed_result(t)
            row.update(pnl=round(res["pnl"], 2), r_multiple=res["r_multiple"] and round(res["r_multiple"], 2))
        w.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=swing_trades.csv"},
    )


@app.get("/api/symbol/{symbol}")
def symbol_info(symbol: str, db: Session = Depends(get_db)):
    """Quick look-up for the add-trade form: last price + trend filter."""
    symbol = normalize_symbol(symbol.strip().upper(), get_settings(db))
    cache = market.get_bars(db, symbol)
    ind = market.indicators(cache.bars)
    return {"symbol": symbol.upper(), **ind, "trend": calc.trend_check(ind), "data_error": cache.error}

