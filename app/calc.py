"""Swing-trade math: position sizing, stops, targets and exit signals (long trades).

Rules implemented (all thresholds come from Settings):
  * Initial stop: user supplied, else entry - ATR_MULT x ATR(14), else entry - DEFAULT_STOP_PCT.
  * 1R = entry - initial stop.  Shares = (account x risk%) / 1R, capped by account size.
  * Target 1 = entry + T1_RR x R  (take partial profit, start trailing)
    Target 2 = entry + T2_RR x R  (exit the rest)
  * Once price has closed >= entry + BREAKEVEN_R x R, the stop moves to breakeven.
  * Once Target 1 has been hit, the stop trails at highest close - ATR_MULT x ATR.
  * Trend exit: close more than 0.5 ATR below the 50-day SMA (just below = warning).
  * Time stop: held longer than MAX_HOLD_DAYS without reaching +1R.
"""

import math
from datetime import date

# Signal severities, used for colouring / sorting in the UI.
EXIT, ACTION, WATCH, HOLD = "exit", "action", "watch", "hold"


def initial_stop(entry: float, settings, atr: float | None) -> tuple[float, str]:
    if atr:
        return round(entry - settings.atr_multiple * atr, 4), f"{settings.atr_multiple:g}×ATR"
    return round(entry * (1 - settings.default_stop_pct / 100), 4), f"{settings.default_stop_pct:g}%"


def position_size(entry: float, stop: float, settings) -> int:
    risk_per_share = entry - stop
    if risk_per_share <= 0 or entry <= 0:
        return 0
    by_risk = math.floor(settings.account_size * settings.risk_pct / 100 / risk_per_share)
    by_capital = math.floor(settings.account_size / entry)
    return max(0, min(by_risk, by_capital))


def plan(entry: float, stop: float, shares: int, settings) -> dict:
    """Static numbers that are known at entry time."""
    r = entry - stop
    return {
        "risk_per_share": r,
        "position_value": entry * shares,
        "max_loss": r * shares,
        "risk_pct_of_account": (r * shares) / settings.account_size * 100 if settings.account_size else None,
        "target1": entry + settings.target1_rr * r,
        "target2": entry + settings.target2_rr * r,
        "breakeven_trigger": entry + settings.breakeven_r * r,
        "stop_pct": r / entry * 100 if entry else None,
    }


def trend_check(ind: dict) -> dict:
    """The strategy's setup filter: price > SMA50 > SMA200, and near the 20/50 SMA for a pullback entry."""
    price, s20, s50, s200 = ind.get("last_close"), ind.get("sma20"), ind.get("sma50"), ind.get("sma200")
    if price is None or s50 is None:
        return {"ok": None, "text": "Not enough data"}
    checks = []
    ok = True
    if price > s50:
        checks.append("Price > SMA50")
    else:
        ok = False
        checks.append("Price < SMA50")
    if s200 is not None:
        if s50 > s200:
            checks.append("SMA50 > SMA200")
        else:
            ok = False
            checks.append("SMA50 < SMA200")
    near = [n for n, v in (("SMA20", s20), ("SMA50", s50)) if v and abs(price - v) / v <= 0.03]
    if near:
        checks.append(f"pullback near {'/'.join(near)}")
    return {"ok": ok, "text": " · ".join(checks)}


def evaluate(trade, settings, ind: dict, today: date | None = None) -> dict:
    """Live management of an open trade: current stop, P&L and what to do now."""
    today = today or date.today()
    p = plan(trade.entry_price, trade.stop_price, trade.shares, settings)
    r = p["risk_per_share"]

    price = trade.manual_price if trade.manual_price is not None else ind.get("last_close")
    price_source = "manual" if trade.manual_price is not None else ("market" if price is not None else None)

    highest_close = max(ind.get("highest_close") or 0, price or 0) or None
    highest_high = max(ind.get("highest_high") or 0, price or 0) or None
    atr = ind.get("atr14")

    # --- current (effective) stop -------------------------------------------------------
    stop = trade.stop_price
    stop_label = "Initial stop"
    be_reached = highest_close is not None and r > 0 and highest_close >= p["breakeven_trigger"]
    t1_reached = highest_high is not None and r > 0 and highest_high >= p["target1"]
    if be_reached and trade.entry_price > stop:
        stop, stop_label = trade.entry_price, "Breakeven"
    trailing = None
    if t1_reached and atr and highest_close:
        trailing = highest_close - settings.atr_multiple * atr
        if trailing > stop:
            stop, stop_label = trailing, "Trailing"

    out = {
        **p,
        "price": price,
        "price_source": price_source,
        "price_date": ind.get("last_date") if price_source == "market" else None,
        "current_stop": stop,
        "stop_label": stop_label,
        "trailing_stop": trailing,
        "highest_close": highest_close,
        "sma20": ind.get("sma20"),
        "sma50": ind.get("sma50"),
        "sma200": ind.get("sma200"),
        "atr14": atr,
        "days_held": (today - trade.entry_date).days,
        "trend": trend_check(ind),
    }

    if price is None:
        out.update(signal=WATCH, action="No price — refresh data or enter a manual price",
                   pnl=None, pnl_pct=None, r_multiple=None, open_risk=None, progress=None)
        return out

    pnl = (price - trade.entry_price) * trade.shares
    r_mult = (price - trade.entry_price) / r if r > 0 else None
    out.update(
        pnl=pnl,
        pnl_pct=(price - trade.entry_price) / trade.entry_price * 100,
        r_multiple=r_mult,
        # what you'd lose from here if the current stop is hit (negative = locked-in profit)
        open_risk=(price - stop) * trade.shares if price > stop else 0.0,
        locked_in=(stop - trade.entry_price) * trade.shares,
        # 0% = at stop, 100% = at target 2
        progress=max(0.0, min(100.0, (price - trade.stop_price) / (p["target2"] - trade.stop_price) * 100))
        if p["target2"] > trade.stop_price else None,
    )

    sma50 = ind.get("sma50")
    trend_buffer = 0.5 * atr if atr else 0.0

    # --- signals, most urgent first ------------------------------------------------------
    if price <= stop:
        sig, act = EXIT, f"EXIT — {stop_label.lower()} hit ({stop:.2f})"
    elif price >= p["target2"]:
        sig, act = EXIT, f"EXIT — Target 2 reached ({p['target2']:.2f}), take full profit"
    elif sma50 is not None and price < sma50 - trend_buffer:
        sig, act = EXIT, f"EXIT — closed clearly below SMA50 ({sma50:.2f}), trend invalidated"
    elif t1_reached:
        sig, act = ACTION, (f"Target 1 hit — sell ~half if not done, trail rest with stop {stop:.2f}")
    elif be_reached:
        sig, act = ACTION, f"Move stop to breakeven ({trade.entry_price:.2f})"
    elif out["days_held"] > settings.max_hold_days and (r_mult or 0) < settings.breakeven_r:
        sig, act = WATCH, f"Time stop — {out['days_held']}d without +{settings.breakeven_r:g}R, consider exiting"
    elif sma50 is not None and price < sma50:
        sig, act = WATCH, f"Below SMA50 ({sma50:.2f}) — exit if it doesn't reclaim it in 1–2 days"
    elif r_mult is not None and r_mult <= -0.75:
        sig, act = WATCH, "Near stop — stick to the plan, don't widen the stop"
    else:
        sig, act = HOLD, f"HOLD — stop {stop:.2f}, target 1 {p['target1']:.2f}"
    out.update(signal=sig, action=act)
    return out


def closed_result(trade) -> dict:
    r = trade.entry_price - trade.stop_price
    pnl = (trade.exit_price - trade.entry_price) * trade.shares
    return {
        "pnl": pnl,
        "pnl_pct": (trade.exit_price - trade.entry_price) / trade.entry_price * 100,
        "r_multiple": (trade.exit_price - trade.entry_price) / r if r > 0 else None,
        "days_held": (trade.exit_date - trade.entry_date).days,
        "risk_per_share": r,
        "max_loss": r * trade.shares,
        "position_value": trade.entry_price * trade.shares,
    }


def journal_stats(closed: list[dict]) -> dict:
    """closed: list of closed_result() dicts."""
    n = len(closed)
    wins = [c for c in closed if c["pnl"] > 0]
    losses = [c for c in closed if c["pnl"] <= 0]
    rs = [c["r_multiple"] for c in closed if c["r_multiple"] is not None]
    gross_win = sum(c["pnl"] for c in wins)
    gross_loss = -sum(c["pnl"] for c in losses)

    # max drawdown of the cumulative realised P&L curve (trades in exit order)
    peak = equity = max_dd = 0.0
    for c in closed:
        equity += c["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n * 100 if n else None,
        "total_pnl": sum(c["pnl"] for c in closed),
        "avg_win": gross_win / len(wins) if wins else None,
        "avg_loss": -gross_loss / len(losses) if losses else None,
        "avg_r": sum(rs) / len(rs) if rs else None,  # expectancy in R
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
        "max_drawdown": max_dd,
        "avg_days_held": sum(c["days_held"] for c in closed) / n if n else None,
    }
