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

Set your account size, risk % and the other rules under **⚙ Settings**.
The defaults follow the strategy: a €10,000 account, 1% risk, targets at 2R and 3R.

| Item | Rule |
|---|---|
| **Stop loss** | Your own stop (ideally below the recent swing low). If you leave it blank: entry − 2 × ATR(14), or 5% below entry when no market data is available |
| **1R** | entry − initial stop |
| **Shares** | (account × risk %) ÷ 1R, capped so the position isn't bigger than the account |
| **Target 1** | entry + 2R → sell about half |
| **Target 2** | entry + 3R → exit the rest |
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
Use Yahoo symbols:

* US: `AAPL`, `MSFT`
* Amsterdam: `ASML.AS`
* Germany: `SAP.DE`
* London: `VOD.L`
* India (NSE): `RELIANCE.NS`

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
