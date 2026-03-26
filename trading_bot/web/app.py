"""
Flask web dashboard for the Binance Futures Testnet Trading Bot.

Provides a browser UI for:
  - Placing MARKET / LIMIT / STOP_MARKET orders
  - TWAP execution
  - Grid trading
  - Live account balance and P&L
  - Order history from SQLite
  - Connectivity status

Run:
    python web/app.py
    python web/app.py --port 5001 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

# Ensure the project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, jsonify, render_template, request

from bot.client import BinanceFuturesClient, BinanceAPIError, BinanceNetworkError
from bot.config import load_config, write_sample_config
from bot.history import record_order, fetch_history, fetch_stats
from bot.logging_config import setup_logging, get_logger
from bot.orders import OrderManager, OrderResult
from bot.strategies.grid import GridExecutor
from bot.strategies.twap import TwapExecutor
from bot.validators import (
    validate_symbol, validate_side, validate_order_type,
    validate_quantity, validate_price, validate_stop_price,
)

logger = get_logger("web.app")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_SORT_KEYS"] = False

# Global shared state (set in main())
_client:   Optional[BinanceFuturesClient] = None
_manager:  Optional[OrderManager]         = None
_dry_run:  bool                           = False
_cfg = None

# TWAP progress tracking
_twap_progress: dict = {}
_twap_lock = threading.Lock()


def get_client() -> BinanceFuturesClient:
    if _client is None:
        raise RuntimeError("Client not initialised.")
    return _client


def get_manager() -> OrderManager:
    if _manager is None:
        raise RuntimeError("Manager not initialised.")
    return _manager


def api_error(message: str, status: int = 400) -> tuple:
    logger.warning("API error response: %s", message)
    return jsonify({"ok": False, "error": message}), status


def api_ok(data: dict) -> tuple:
    return jsonify({"ok": True, **data}), 200


# ──────────────────────────────────────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", dry_run=_dry_run)


# ──────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    try:
        get_client().ping()
        server_time = get_client().get_server_time().get("serverTime")
        return api_ok({"connected": True, "server_time": server_time, "dry_run": _dry_run})
    except Exception as e:
        return api_ok({"connected": False, "error": str(e), "dry_run": _dry_run})


@app.route("/api/account")
def api_account():
    try:
        account = get_client().get_account()
        positions = [
            p for p in account.get("positions", [])
            if float(p.get("positionAmt", 0)) != 0
        ]
        return api_ok({
            "wallet_balance":    account.get("totalWalletBalance"),
            "available_balance": account.get("availableBalance"),
            "unrealized_pnl":    account.get("totalUnrealizedProfit"),
            "can_trade":         account.get("canTrade"),
            "positions":         positions,
        })
    except BinanceAPIError as e:
        # -2014 / -2015 → bad or missing API key
        cred_hint = None
        if e.code in (-2014, -2015, -1022):
            cred_hint = (
                "API key error — make sure you exported your credentials in the "
                "SAME terminal where you launched the server:\n"
                "  export BINANCE_TESTNET_API_KEY=\'your_key\'\n"
                "  export BINANCE_TESTNET_API_SECRET=\'your_secret\'"
            )
        return jsonify({"ok": False, "error": str(e), "cred_hint": cred_hint}), 502
    except BinanceNetworkError as e:
        return api_error(str(e), 502)


@app.route("/api/open_orders")
def api_open_orders():
    symbol = request.args.get("symbol")
    try:
        orders = get_client().get_open_orders(symbol=symbol or None)
        return api_ok({"orders": orders})
    except (BinanceAPIError, BinanceNetworkError) as e:
        return api_error(str(e), 502)


@app.route("/api/order", methods=["POST"])
def api_place_order():
    body = request.get_json(silent=True) or {}
    try:
        symbol     = validate_symbol(body.get("symbol", ""))
        side       = validate_side(body.get("side", ""))
        order_type = validate_order_type(body.get("order_type", ""))
        quantity   = validate_quantity(body.get("quantity", ""))
        price      = validate_price(body.get("price") or None, order_type)
        stop_price = validate_stop_price(body.get("stop_price") or None, order_type)
    except ValueError as e:
        return api_error(str(e))

    if _dry_run:
        result = OrderResult(
            success=True, order_id=999000001, symbol=symbol, side=side,
            order_type=order_type, status="FILLED (simulated)",
            orig_qty=str(quantity), executed_qty=str(quantity), avg_price="0 (dry run)",
        )
    else:
        result = get_manager().place_order(
            symbol=symbol, side=side, order_type=order_type,
            quantity=quantity, price=price, stop_price=stop_price,
        )

    record_order(result, symbol=symbol, side=side, order_type=order_type,
                 quantity=str(quantity),
                 price=str(price) if price else None,
                 stop_price=str(stop_price) if stop_price else None,
                 strategy="manual", dry_run=_dry_run)

    if result.success:
        return api_ok({
            "order_id":     result.order_id,
            "status":       result.status,
            "executed_qty": result.executed_qty,
            "avg_price":    result.avg_price,
            "dry_run":      _dry_run,
        })
    return api_error(result.error_message or "Order failed", 502)


@app.route("/api/cancel", methods=["POST"])
def api_cancel_order():
    body = request.get_json(silent=True) or {}
    try:
        symbol   = validate_symbol(body.get("symbol", ""))
        order_id = int(body.get("order_id", 0))
    except (ValueError, TypeError) as e:
        return api_error(str(e))

    result = get_manager().cancel_order(symbol=symbol, order_id=order_id)
    if result.success:
        return api_ok({"order_id": result.order_id, "status": result.status})
    return api_error(result.error_message or "Cancel failed", 502)


@app.route("/api/twap", methods=["POST"])
def api_twap():
    body = request.get_json(silent=True) or {}
    try:
        symbol   = validate_symbol(body.get("symbol", ""))
        side     = validate_side(body.get("side", ""))
        total    = validate_quantity(body.get("total_qty", ""))
        slices   = int(body.get("slices", 0))
        interval = int(body.get("interval", 0))
        if slices < 2:
            raise ValueError("Slices must be >= 2.")
        if interval < 1 and not _dry_run:
            raise ValueError("Interval must be >= 1 second.")
    except (ValueError, TypeError) as e:
        return api_error(str(e))

    run_id = f"twap_{datetime.now(timezone.utc).strftime('%H%M%S%f')}"

    with _twap_lock:
        _twap_progress[run_id] = {"status": "running", "slices_done": 0,
                                   "slices_total": slices, "log": []}

    def run_twap():
        def on_slice(i, sl):
            entry = {
                "slice": i,
                "qty":   str(sl.quantity),
                "ok":    sl.result.success,
                "info":  str(sl.result.order_id) if sl.result.success else sl.result.error_message,
            }
            with _twap_lock:
                _twap_progress[run_id]["slices_done"] = i
                _twap_progress[run_id]["log"].append(entry)

        executor = TwapExecutor(get_manager(), dry_run=_dry_run, on_slice=on_slice)
        result = executor.run(
            symbol=symbol, side=side, total_qty=Decimal(str(total)),
            slices=slices, interval_seconds=interval,
        )
        for sl in result.completed_slices + result.failed_slices:
            record_order(sl.result, symbol=symbol, side=side, order_type="MARKET",
                         quantity=str(sl.quantity), strategy="twap", dry_run=_dry_run)
        with _twap_lock:
            _twap_progress[run_id]["status"]       = "done" if not result.aborted else "aborted"
            _twap_progress[run_id]["executed_qty"] = str(result.executed_qty)

    t = threading.Thread(target=run_twap, daemon=True)
    t.start()

    return api_ok({"run_id": run_id, "message": f"TWAP started ({slices} slices × {interval}s)"})


@app.route("/api/twap/<run_id>")
def api_twap_status(run_id: str):
    with _twap_lock:
        data = _twap_progress.get(run_id)
    if data is None:
        return api_error("Unknown run_id", 404)
    return api_ok(data)


@app.route("/api/grid", methods=["POST"])
def api_grid():
    body = request.get_json(silent=True) or {}
    try:
        symbol = validate_symbol(body.get("symbol", ""))
        centre = validate_price(body.get("centre_price", ""), "LIMIT")
        step   = validate_price(body.get("step", ""), "LIMIT")
        levels = int(body.get("levels", 0))
        qty    = validate_quantity(body.get("qty_per_level", ""))
        if levels < 1:
            raise ValueError("Levels must be >= 1.")
    except (ValueError, TypeError) as e:
        return api_error(str(e))

    executor = GridExecutor(get_manager(), dry_run=_dry_run)
    result   = executor.run(
        symbol=symbol, centre_price=Decimal(str(centre)), step=Decimal(str(step)),
        levels=levels, qty_per_level=Decimal(str(qty)),
    )

    for lvl in result.grid_levels:
        if lvl.result:
            record_order(lvl.result, symbol=symbol, side=lvl.side, order_type="LIMIT",
                         quantity=str(lvl.quantity), price=str(lvl.price),
                         strategy="grid", dry_run=_dry_run)

    placed = [
        {"side": l.side, "price": str(l.price), "qty": str(l.quantity),
         "order_id": l.result.order_id if l.result and l.result.success else None,
         "status":   l.result.status   if l.result else "not placed"}
        for l in result.grid_levels
    ]
    return api_ok({
        "placed_count": result.placed_count,
        "failed_count": result.failed_count,
        "levels":       placed,
        "dry_run":      _dry_run,
    })



@app.route("/api/credentials_check")
def api_credentials_check():
    """
    Quick check: are credentials present and do they authenticate?
    Returns a specific hint when keys are missing or wrong.
    """
    key    = _cfg.api_key    if _cfg else ""
    secret = _cfg.api_secret if _cfg else ""

    if not key or not secret or key == "DRY_RUN_KEY":
        return api_ok({
            "ok": False,
            "reason": "missing",
            "hint": (
                "No API credentials loaded. Export them in the terminal where "
                "you started the server, then restart:\n"
                "  export BINANCE_TESTNET_API_KEY=\'your_key\'\n"
                "  export BINANCE_TESTNET_API_SECRET=\'your_secret\'"
            ),
            "dry_run": _dry_run,
        })

    # Credentials present — try a lightweight authenticated call
    try:
        get_client().get_account()
        return api_ok({"ok": True, "dry_run": _dry_run})
    except BinanceAPIError as e:
        hint = None
        if e.code in (-2014, -2015, -1022):
            hint = (
                "Authentication failed. Common causes:\n"
                "  1. Wrong key/secret — double-check them on testnet.binancefuture.com\n"
                "  2. Keys exported in a different terminal session\n"
                "  3. Extra spaces or quotes around the key values"
            )
        return api_ok({"ok": False, "reason": "auth_error", "hint": hint or str(e), "dry_run": _dry_run})
    except BinanceNetworkError as e:
        return api_ok({"ok": False, "reason": "network", "hint": str(e), "dry_run": _dry_run})


@app.route("/api/history")
def api_history():
    symbol   = request.args.get("symbol")
    strategy = request.args.get("strategy")
    limit    = int(request.args.get("limit", 50))
    rows     = fetch_history(symbol=symbol, strategy=strategy, limit=limit)
    stats    = fetch_stats(symbol=symbol, strategy=strategy)
    return api_ok({"rows": rows, "stats": stats})


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    global _client, _manager, _dry_run, _cfg

    parser = argparse.ArgumentParser(description="Trading Bot — Web Dashboard")
    parser.add_argument("--port",      type=int, default=5000)
    parser.add_argument("--host",      default="127.0.0.1")
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--log-level", default=None)
    parser.add_argument("--config",    default=None)
    args = parser.parse_args()

    _cfg     = load_config(config_path=args.config)
    _dry_run = args.dry_run or _cfg.dry_run
    log_lvl  = args.log_level or _cfg.log_level

    setup_logging(log_lvl)
    write_sample_config()

    if not _cfg.api_key or not _cfg.api_secret:
        if not _dry_run:
            print()
            print("  ✗  API credentials not found.")
            print()
            print("  The dashboard needs your Binance Futures Testnet keys.")
            print("  Export them in the SAME terminal before running:")
            print()
            print("    export BINANCE_TESTNET_API_KEY=\'your_key\'")
            print("    export BINANCE_TESTNET_API_SECRET=\'your_secret\'")
            print()
            print("  Or add them to config.toml (see README) or a .env file.")
            print()
            sys.exit(1)
        _cfg.api_key    = "DRY_RUN_KEY"
        _cfg.api_secret = "DRY_RUN_SECRET"
        print("⚠  Running in DRY-RUN mode — no real API calls.")

    _client  = BinanceFuturesClient(api_key=_cfg.api_key, api_secret=_cfg.api_secret,
                                     base_url=_cfg.base_url)
    _manager = OrderManager(_client)

    mode = "DRY-RUN" if _dry_run else "LIVE"
    print(f"\n  Trading Bot Dashboard [{mode}]")
    print(f"  Open: http://{args.host}:{args.port}/\n")
    logger.info("Web dashboard starting | host=%s port=%d dry_run=%s", args.host, args.port, _dry_run)

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
