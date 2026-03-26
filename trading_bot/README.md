# Binance Futures Testnet — Trading Bot (Enhanced)

A Python application for trading on the **Binance USDT-M Futures Testnet**, with a full set of bonus features: TWAP, Grid, interactive menu CLI, web dashboard, order history, live P&L, dry-run mode, and config file support.

---

## Feature Overview

| Feature | Entry point |
|---|---|
| Place MARKET / LIMIT / STOP_MARKET orders | `cli.py order` or `menu.py [1]` or web UI |
| **TWAP execution** | `cli.py twap` or `menu.py [2]` or web UI |
| **Grid trading** | `cli.py grid` or `menu.py [3]` or web UI |
| **Interactive menu CLI** | `python menu.py` |
| **Lightweight web dashboard** | `python web/app.py` |
| **Order history (SQLite)** | `cli.py history` or `menu.py [7]` or web UI |
| **Live P&L / positions** | `cli.py account` or `menu.py [6]` or web UI |
| **Dry-run simulation** | `--dry-run` flag on any entry point |
| **Config file support** | `config.toml` + `.env` + env vars |

---

## Project Structure

```
trading_bot/
├── bot/
│   ├── client.py           # Binance REST client (HMAC auth, retries, error types)
│   ├── orders.py           # OrderManager + OrderResult dataclass
│   ├── validators.py       # Input validation (raises ValueError on bad input)
│   ├── logging_config.py   # Rotating file + console logging
│   ├── history.py          # SQLite order history tracker
│   ├── config.py           # Config loader: .env → config.toml → env vars
│   └── strategies/
│       ├── twap.py         # TWAP execution strategy
│       └── grid.py         # Grid trading strategy
├── web/
│   ├── app.py              # Flask REST API + web dashboard server
│   └── templates/
│       └── index.html      # Single-page dashboard UI
├── cli.py                  # Flag-based CLI (argparse)
├── menu.py                 # Interactive menu-driven CLI
├── config.toml             # Configuration file (edit this)
├── logs/
│   ├── trading_bot.log     # Rotating log (auto-created)
│   └── orders.db           # SQLite history (auto-created)
└── requirements.txt
```

---

## Setup

### 1. Python 3.9+
```bash
python --version
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Binance Futures Testnet credentials
1. Go to [https://testnet.binancefuture.com](https://testnet.binancefuture.com)
2. Log in → **API Management** → **Generate Key**
3. Copy **API Key** and **Secret Key**

### 4. Set credentials — choose one method

**A. Environment variables (recommended)**

*On Windows (PowerShell):*
```powershell
$env:BINANCE_TESTNET_API_KEY="your_api_key_here"
$env:BINANCE_TESTNET_API_SECRET="your_secret_key_here"
```

*On Linux / macOS:*
```bash
export BINANCE_TESTNET_API_KEY="your_api_key_here"
export BINANCE_TESTNET_API_SECRET="your_secret_key_here"
```

**B. `.env` file** (project root)
```
BINANCE_TESTNET_API_KEY=your_key
BINANCE_TESTNET_API_SECRET=your_secret
```

**C. `config.toml`** (uncomment lines)
```toml
[bot]
api_key    = "your_key"
api_secret = "your_secret"
```

---

## Web Dashboard (Flask)

```bash
python web/app.py                # http://127.0.0.1:5000
python web/app.py --port 5001    # custom port
python web/app.py --dry-run      # simulation mode
```

The dashboard provides:
- **Place Order** tab — MARKET / LIMIT / STOP_MARKET with inline cancel
- **TWAP** tab — configure and start a TWAP run with live progress bar
- **Grid** tab — preview and place a grid with visual level layout
- **History** tab — filterable table of all recorded orders
- **Left sidebar** — live account balance, unrealised P&L, open positions, open orders
- Auto-refreshes every 15 seconds

---

## Interactive Menu CLI

```bash
python menu.py                   # real mode
python menu.py --dry-run         # simulate all orders
python menu.py --log-level DEBUG # verbose console output
```

```
  [1]  Place Order           (MARKET / LIMIT / STOP_MARKET)
  [2]  TWAP Execution        (time-sliced large order)
  [3]  Grid Trading          (ladder buy + sell limits)
  [4]  Open Orders
  [5]  Cancel Order
  [6]  Live Positions & P&L
  [7]  Order History         (SQLite)
  [8]  Connectivity Ping
  [q]  Quit
```

---

## Flag-based CLI

### Place orders
```bash
python cli.py order --symbol BTCUSDT --side BUY  --type MARKET --qty 0.001
python cli.py order --symbol ETHUSDT --side SELL --type LIMIT  --qty 0.01 --price 3450
python cli.py order --symbol BTCUSDT --side BUY  --type STOP_MARKET --qty 0.001 --stop-price 70000
python cli.py order --symbol BTCUSDT --side BUY  --type MARKET --qty 0.001 --dry-run
```

### TWAP
```bash
# 5 market BUY slices of 0.01 BTC, 60 seconds apart
python cli.py twap --symbol BTCUSDT --side BUY --total-qty 0.01 --slices 5 --interval 60
python cli.py twap --symbol BTCUSDT --side BUY --total-qty 0.01 --slices 5 --interval 60 --dry-run
```

### Grid
```bash
# 3 buy + 3 sell LIMIT orders, $500 apart, 0.001 BTC each
python cli.py grid --symbol BTCUSDT --centre 68000 --step 500 --levels 3 --qty 0.001
python cli.py grid --symbol BTCUSDT --centre 68000 --step 500 --levels 3 --qty 0.001 --dry-run
```

### Account, orders, history
```bash
python cli.py account
python cli.py open-orders --symbol BTCUSDT
python cli.py cancel --symbol BTCUSDT --order-id 3871209812
python cli.py history
python cli.py history --symbol ETHUSDT --limit 50
python cli.py ping
```

---

## Configuration (`config.toml`)

```toml
[bot]
# api_key    = ""   # prefer env var BINANCE_TESTNET_API_KEY
# api_secret = ""   # prefer env var BINANCE_TESTNET_API_SECRET

base_url       = "https://testnet.binancefuture.com"
recv_window    = 5000
log_level      = "INFO"    # DEBUG | INFO | WARNING | ERROR
dry_run        = false
default_symbol = "BTCUSDT"
```

**Priority**: env var > config.toml > .env file > default

| Env var | TOML key | Default |
|---|---|---|
| `BINANCE_TESTNET_API_KEY` | `api_key` | — |
| `BINANCE_TESTNET_API_SECRET` | `api_secret` | — |
| `BINANCE_BASE_URL` | `base_url` | testnet URL |
| `BOT_LOG_LEVEL` | `log_level` | `INFO` |
| `BOT_DRY_RUN` | `dry_run` | `false` |
| `BOT_DEFAULT_SYMBOL` | `default_symbol` | `BTCUSDT` |

---

## Dry-Run Mode

All three entry points support `--dry-run`. In dry-run mode:
- No HTTP requests are made to Binance
- Orders are simulated with a `FILLED (simulated)` status
- Results are still recorded to `logs/orders.db` with `dry_run=1`
- Useful for testing strategies and validating logic

```bash
python cli.py  order --symbol BTCUSDT --side BUY --type MARKET --qty 0.001 --dry-run
python menu.py --dry-run
python web/app.py --dry-run
```

---

## Order History (SQLite)

Every order placed (or simulated) is persisted to `logs/orders.db`.

```bash
python cli.py history                      # last 20 orders
python cli.py history --symbol BTCUSDT    # filtered
python cli.py history --limit 100
```

Schema: `id, order_id, symbol, side, order_type, strategy, quantity, price, stop_price, executed_qty, avg_price, status, dry_run, error, created_at`

---

## Logging

Log file: `logs/trading_bot.log` (rotating: 5 MB × 5 backups)

| Level | Logged |
|---|---|
| `DEBUG` | Full request params (signature redacted), raw response JSON |
| `INFO` | Order lifecycle, TWAP/grid slice progress |
| `WARNING` | Validation failures, TWAP aborts |
| `ERROR` | API errors (code + message), network failures |

---

## TWAP Strategy

- Splits parent order into N equal MARKET slices
- Last slice absorbs any rounding remainder (no dust left behind)
- Configurable `abort_on_failure` — stops on first failed slice
- Web UI shows live progress bar and per-slice log
- All slices recorded individually in history with `strategy="twap"`

## Grid Strategy

- Places `levels` BUY LIMIT orders below centre and `levels` SELL LIMIT orders above
- Step-spaced levels: `2 × levels` total orders
- Web UI shows visual grid preview (red sells above, green buys below) before placing
- All levels recorded with `strategy="grid"`

---

## Architecture

```
Entry points (cli.py / menu.py / web/app.py)
    ↓  validated Python types
bot/orders.py  (OrderManager)
    ↓  string params
bot/client.py  (BinanceFuturesClient — HMAC, retries, error types)
    ↓  signed REST
Binance Futures Testnet API
    ↑  persisted to
bot/history.py  (SQLite — logs/orders.db)
```

---

## Assumptions

- **Testnet only** — base URL defaults to `https://testnet.binancefuture.com`
- **USDT-M Futures** — only `/fapi/` endpoints used
- **Quantity precision** — use the symbol's LOT_SIZE step (e.g. 0.001 for BTC)
- **Position mode** — uses account default (One-way or Hedge)
- **No Binance SDK** — all calls are direct REST via `requests`

---

## Dependencies

| Package | Purpose |
|---|---|
| `requests` | HTTP with retry adapter |
| `urllib3` | Retry policy (bundled with requests) |
| `colorama` | Coloured output in `menu.py` |
| `flask` | Web dashboard server |
| `tomli` | TOML config on Python < 3.11 (stdlib `tomllib` on 3.11+) |
