"""Market data: download daily bars (Yahoo Finance via yfinance) and compute indicators."""

import logging
import os
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from db import PriceCache

log = logging.getLogger(__name__)

CACHE_MINUTES = int(os.getenv("PRICE_CACHE_MINUTES", "15"))


def _download(symbol: str, start: date) -> list[dict]:
    import yfinance as yf

    df = yf.Ticker(symbol).history(start=start.isoformat(), interval="1d", auto_adjust=False)
    if df is None or df.empty:
        raise ValueError(f"No data returned for '{symbol}'")
    bars = []
    for idx, row in df.iterrows():
        if row["Close"] != row["Close"]:  # NaN
            continue
        bars.append(
            {
                "d": idx.date().isoformat(),
                "h": float(row["High"]),
                "l": float(row["Low"]),
                "c": float(row["Close"]),
            }
        )
    if not bars:
        raise ValueError(f"No usable bars for '{symbol}'")
    return bars


def get_bars(db: Session, symbol: str, entry_date: date | None = None, force: bool = False) -> PriceCache:
    """Return cached bars for symbol, refreshing when stale or when history doesn't reach back far enough."""
    symbol = symbol.upper()
    # 200-SMA needs ~200 trading days (~290 calendar days) before the entry date.
    need_from = min(entry_date or date.today(), date.today()) - timedelta(days=420)
    cached = db.get(PriceCache, symbol)

    fresh = cached is not None and datetime.utcnow() - cached.fetched_at < timedelta(minutes=CACHE_MINUTES)
    covers = cached is not None and cached.bars and cached.bars[0]["d"] <= (need_from + timedelta(days=10)).isoformat()
    if cached is not None and not force and fresh and (covers or cached.error):
        return cached

    try:
        bars = _download(symbol, need_from)
        error = None
    except Exception as exc:  # network down, bad symbol, provider change...
        log.warning("Price fetch failed for %s: %s", symbol, exc)
        bars = cached.bars if cached is not None else []
        error = str(exc)

    if cached is None:
        cached = PriceCache(symbol=symbol, fetched_at=datetime.utcnow(), bars=bars, error=error)
        db.add(cached)
    else:
        cached.fetched_at = datetime.utcnow()
        cached.bars = bars
        cached.error = error
    db.commit()
    return cached


def _sma(closes: list[float], n: int) -> float | None:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def _atr(bars: list[dict], n: int = 14) -> float | None:
    if len(bars) < n + 1:
        return None
    trs = []
    for prev, cur in zip(bars[-n - 1 : -1], bars[-n:]):
        trs.append(max(cur["h"] - cur["l"], abs(cur["h"] - prev["c"]), abs(cur["l"] - prev["c"])))
    return sum(trs) / n


def indicators(bars: list[dict], entry_date: date | None = None, as_of: date | None = None) -> dict:
    """Indicators using bars up to `as_of` (inclusive); extremes measured since `entry_date`."""
    if as_of is not None:
        bars = [b for b in bars if b["d"] <= as_of.isoformat()]
    if not bars:
        return {}
    closes = [b["c"] for b in bars]
    out = {
        "last_close": closes[-1],
        "last_date": bars[-1]["d"],
        "sma20": _sma(closes, 20),
        "sma50": _sma(closes, 50),
        "sma200": _sma(closes, 200),
        "atr14": _atr(bars, 14),
    }
    if entry_date is not None:
        since = [b for b in bars if b["d"] >= entry_date.isoformat()]
        if since:
            out["highest_close"] = max(b["c"] for b in since)
            out["highest_high"] = max(b["h"] for b in since)
            out["lowest_low"] = min(b["l"] for b in since)
    return out
