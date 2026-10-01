# Swing Trading Dashboard

A local dashboard for planning and managing swing trades with the
**trend + pullback** strategy. You enter **symbol, entry date and entry price**.
It works out the stop loss, position size, targets and a "what to do now" signal
for each open position. Everything is stored in PostgreSQL.

## Run it

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose).

```bash
git clone <this repo> && cd dashboard_swing_trading
docker compose up -d --build
```

Open **http://localhost:8000**

### "port is already allocated"?

Another program is already using port 8000. Pick a different port by creating a
`.env` file next to `docker-compose.yml`:

```bash
echo APP_PORT=8080 > .env
docker compose up -d
```

Then open **http://localhost:8080** instead.

| Task | Command |
|---|---|
| Stop | `docker compose down` (your data is kept) |
| Start again | `docker compose up -d` |
| Logs | `docker compose logs -f app` |
| Wipe all data | `docker compose down -v` |

Trades live in the `pgdata` Docker volume, so they survive restarts and rebuilds.
Both containers use `restart: unless-stopped`, so the dashboard comes back up
automatically when Docker starts.

## What it calculates

Set your starting capital, risk % and the other rules under **⚙ Settings**.
The defaults: ₹1,00,000 starting capital, 1% risk, targets at 2R and 3R,
and NSE symbols (`.NS`).

| Item | Rule |
|---|---|
| **Stop loss** | Your own stop (ideally below the recent swing low). If you leave it blank: entry − 2 × ATR(14), or 5% below entry when no market data is available |
| **1R** | entry − initial stop |
| **Shares** | (equity × risk %) ÷ 1R, capped by the cash still available |
| **Capital** | Equity = starting capital + realised profit/loss. Available cash = equity − money in open trades, so it goes down with every trade you add |
| **Target 1** | entry + 2R → sell about half |
| **Target 2** | entry + 3R → exit the rest |
| **Profit** | What you'd earn at Target 1, at Target 2, and with the planned exit (half at T1, half at T2). Shown per trade and in total |
| **Your target** | Optional target price per trade: shows the profit and R:R at that price, and signals EXIT when it's reached |
| **Breakeven** | after a close at +1R, move the stop to the entry price |
| **Trailing stop** | after Target 1, the stop trails at highest close − 2 × ATR |
| **Trend exit** | a close more than 0.5 ATR below the 50-day SMA (just below it gives a warning) |
| **Time stop** | more than 30 days held without reaching +1R |
| **Trend filter** | price > SMA50 > SMA200, and a pullback near the 20/50 SMA |

Each open position gets one signal, ordered by urgency:

* 🔴 **EXIT**: stop hit, Target 2 hit, or trend broken
* 🟠 **ACTION**: move the stop to breakeven, or take partial profit at Target 1
* ⚪ **WATCH**: time stop, near the stop, just below the SMA50, or no price data
* 🟢 **HOLD**: the plan is unchanged

The **Closed / journal** tab and the top KPIs track win rate, expectancy
(average R), average win/loss, profit factor and max drawdown. **Export CSV**
downloads every trade.

## Prices

Daily prices come from Yahoo Finance through `yfinance`. They are cached in the
database for 15 minutes. Click **↻ Refresh prices** to force an update.
Just type the NSE symbol (`RELIANCE`, `TCS`, `INFY`) and `.NS` is added for you.
Change the **Exchange suffix** in Settings to `.BO` for BSE, or clear it to type
full Yahoo symbols (`AAPL`, `ASML.AS`). Prices are not converted between
currencies, so trade stocks in the same currency as your account.

If there is no data (no internet, or an unknown symbol), open **Edit** on the
trade and enter a **manual current price**. The signals still work.

## Running without Docker (optional)

```bash
cd app
pip install -r requirements.txt
uvicorn main:app --reload      # uses a local SQLite file (swing.db)
python -m pytest tests         # unit tests for the trading math
```

---

Educational tool, not financial advice. Long trades only. Fees and slippage are not modelled.
