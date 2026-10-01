from datetime import date, timedelta
from types import SimpleNamespace

import calc
import market

S = SimpleNamespace(
    account_size=10000, risk_pct=1, target1_rr=2, target2_rr=3, breakeven_r=1,
    atr_multiple=2, default_stop_pct=5, max_hold_days=30, max_open_positions=5,
)


def bars_from(closes, start=date(2026, 1, 1), spread=1.0):
    return [
        {"d": (start + timedelta(days=i)).isoformat(), "h": c + spread, "l": c - spread, "c": c}
        for i, c in enumerate(closes)
    ]


def trade(**kw):
    base = dict(entry_price=100.0, stop_price=95.0, shares=20, entry_date=date(2026, 9, 1), manual_price=None,
                target_price=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_position_size_from_risk():
    assert calc.position_size(50, 48, S) == 50  # €100 risk / €2
    assert calc.position_size(100, 99.9, S) == 100  # capped by account size


def test_plan_targets():
    p = calc.plan(50, 48, 50, S)
    assert p["target1"] == 54 and p["target2"] == 56 and p["max_loss"] == 100


def test_initial_stop_atr_and_fallback():
    assert calc.initial_stop(100, S, 2.5) == (95.0, "2×ATR")
    assert calc.initial_stop(100, S, None) == (95.0, "5%")


def test_indicators():
    ind = market.indicators(bars_from([float(i) for i in range(1, 251)]))
    assert ind["sma50"] == sum(range(201, 251)) / 50
    assert ind["sma200"] is not None and abs(ind["atr14"] - 2.0) < 1e-9


def test_signals_manual_price():
    today = date(2026, 9, 10)
    assert calc.evaluate(trade(manual_price=101), S, {}, today)["signal"] == calc.HOLD
    assert calc.evaluate(trade(manual_price=94), S, {}, today)["signal"] == calc.EXIT
    r = calc.evaluate(trade(manual_price=106), S, {}, today)
    assert r["signal"] == calc.ACTION and r["current_stop"] == 100 and r["stop_label"] == "Breakeven"
    assert calc.evaluate(trade(manual_price=116), S, {}, today)["signal"] == calc.EXIT  # target 2
    assert calc.evaluate(trade(manual_price=101), S, {}, date(2026, 10, 15))["signal"] == calc.WATCH  # time stop


def test_trailing_and_trend_exit():
    # long uptrend then pullback after hitting T1 (110)
    closes = [60 + i * 0.2 for i in range(240)] + [100, 104, 108, 111, 112, 109]
    b = bars_from(closes, start=date(2026, 9, 1) - timedelta(days=240))
    ind = market.indicators(b, entry_date=date(2026, 9, 1))
    r = calc.evaluate(trade(), S, ind, date(2026, 9, 7))
    assert r["stop_label"] == "Trailing" and r["current_stop"] > 100
    assert r["signal"] == calc.ACTION
    # price falls well below the SMA50 -> trend exit
    low = trade(stop_price=50.0, manual_price=80.0)
    assert "SMA50" in calc.evaluate(low, S, ind, date(2026, 9, 7))["action"]


def test_journal_stats():
    s = calc.journal_stats([
        {"pnl": 200, "r_multiple": 2, "days_held": 10},
        {"pnl": -100, "r_multiple": -1, "days_held": 5},
        {"pnl": -100, "r_multiple": -1, "days_held": 3},
    ])
    assert abs(s["win_rate"] - 100 / 3) < 1e-9 and s["profit_factor"] == 1 and s["max_drawdown"] == 200 and s["avg_r"] == 0


def test_position_size_capped_by_available_cash():
    # ₹1,00,000 equity, 1% risk = ₹1,000; ₹10 risk/share -> 100 shares, but only ₹5,000 cash left
    assert calc.position_size(100, 90, S, equity=100000, available=100000) == 100
    assert calc.position_size(100, 90, S, equity=100000, available=5000) == 50
    assert calc.position_size(100, 90, S, equity=100000, available=-10) == 0


def test_capital_reduces_with_open_trades():
    c = calc.capital(S, realised_pnl=500, deployed=4000)
    assert c == {"equity": 10500, "capital_deployed": 4000, "available_capital": 6500}


def test_profit_at_targets():
    p = calc.plan(100, 95, 21, S, target=112)
    assert p["profit_target1"] == 210 and p["profit_target2"] == 315
    assert p["profit_plan"] == 10 * 10 + 11 * 15  # 10 shares sold at T1, 11 at T2
    assert p["profit_target"] == 252 and p["target_rr"] == 2.4


def test_own_target_exit_signal():
    r = calc.evaluate(trade(target_price=108, manual_price=108.5), S, {}, date(2026, 9, 10))
    assert r["signal"] == calc.EXIT and "your target" in r["action"]
    assert r["to_target2"] == (115 - 108.5) * 20
