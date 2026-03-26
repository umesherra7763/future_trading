#!/usr/bin/env python3
"""
Binance Futures Testnet Trading Bot — CLI entry point.

Usage examples
--------------
# MARKET BUY
python cli.py order --symbol BTCUSDT --side BUY --type MARKET --qty 0.001

# LIMIT SELL
python cli.py order --symbol ETHUSDT --side SELL --type LIMIT --qty 0.01 --price 3500

# STOP_MARKET BUY (closes a short position when price rises to 70000)
python cli.py order --symbol BTCUSDT --side BUY --type STOP_MARKET --qty 0.001 --stop-price 70000

# Cancel an existing open order
python cli.py cancel --symbol BTCUSDT --order-id 123456789

# Check open orders
python cli.py open-orders --symbol BTCUSDT

# Account balance summary
python cli.py account
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from typing import Optional

from bot.client import BinanceFuturesClient, BinanceAPIError, BinanceNetworkError
from bot.config import load_config, write_sample_config
from bot.history import record_order, fetch_history, fetch_stats
from bot.logging_config import setup_logging, get_logger, LOG_FILE
from bot.orders import OrderManager, OrderResult
from bot.strategies.grid import GridExecutor
from bot.strategies.twap import TwapExecutor
from bot.validators import validate_all, validate_symbol, validate_side, validate_order_type, validate_quantity, validate_price
from bot.logging_config import setup_logging, get_logger, LOG_FILE
from bot.orders import OrderManager
from bot.validators import validate_all, validate_symbol, validate_side, validate_order_type

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

BANNER = """\
╔══════════════════════════════════════════════════════╗
║     Binance Futures Testnet — Trading Bot CLI        ║
╚══════════════════════════════════════════════════════╝"""

SEP = "─" * 54


def print_banner() -> None:
    print(BANNER)


def print_section(title: str) -> None:
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def print_success(msg: str) -> None:
    print(f"\n  ✔  {msg}")


def print_failure(msg: str) -> None:
    print(f"\n  ✗  {msg}", file=sys.stderr)


def get_credentials() -> tuple[str, str]:
    """
    Read API credentials from environment variables.

    Raises:
        SystemExit: If either variable is missing.
    """
    api_key = os.getenv("BINANCE_TESTNET_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_TESTNET_API_SECRET", "").strip()

    missing = []
    if not api_key:
        missing.append("BINANCE_TESTNET_API_KEY")
    if not api_secret:
        missing.append("BINANCE_TESTNET_API_SECRET")

    if missing:
        print_failure(
            f"Missing environment variable(s): {', '.join(missing)}\n"
            "  Export them before running:\n"
            "    export BINANCE_TESTNET_API_KEY=<your-key>\n"
            "    export BINANCE_TESTNET_API_SECRET=<your-secret>"
        )
        sys.exit(1)

    return api_key, api_secret


# ──────────────────────────────────────────────────────────────────────────────
# Sub-command handlers
# ──────────────────────────────────────────────────────────────────────────────

def cmd_order(args: argparse.Namespace, client: BinanceFuturesClient, logger) -> int:
    """Handle the 'order' sub-command."""

    # Validate all inputs; surface helpful messages on failure
    try:
        params = validate_all(
            symbol=args.symbol,
            side=args.side,
            order_type=args.type,
            quantity=args.qty,
            price=args.price,
            stop_price=args.stop_price,
        )
    except ValueError as exc:
        print_failure(f"Validation error: {exc}")
        logger.warning("Validation failed: %s", exc)
        return 1

    # Echo the validated request back to the user
    print_section("Order Request")
    print(f"  Symbol     : {params['symbol']}")
    print(f"  Side       : {params['side']}")
    print(f"  Type       : {params['order_type']}")
    print(f"  Quantity   : {params['quantity']}")
    if params["price"] is not None:
        print(f"  Price      : {params['price']}")
    if params["stop_price"] is not None:
        print(f"  Stop Price : {params['stop_price']}")

    # Place the order
    manager = OrderManager(client)
    result = manager.place_order(
        symbol=params["symbol"],
        side=params["side"],
        order_type=params["order_type"],
        quantity=params["quantity"],
        price=params["price"],
        stop_price=params["stop_price"],
        reduce_only=args.reduce_only,
    )

    print_section("Order Response")
    for line in result.summary_lines():
        print(line)

    if result.success:
        print_success("Order placed successfully.")
        return 0
    else:
        print_failure("Order placement failed.")
        return 1


def cmd_cancel(args: argparse.Namespace, client: BinanceFuturesClient, logger) -> int:
    """Handle the 'cancel' sub-command."""
    try:
        symbol = validate_symbol(args.symbol)
    except ValueError as exc:
        print_failure(f"Validation error: {exc}")
        return 1

    print_section("Cancel Request")
    print(f"  Symbol   : {symbol}")
    print(f"  Order ID : {args.order_id}")

    manager = OrderManager(client)
    result = manager.cancel_order(symbol=symbol, order_id=args.order_id)

    print_section("Cancel Response")
    for line in result.summary_lines():
        print(line)

    if result.success:
        print_success("Order cancelled successfully.")
        return 0
    else:
        print_failure("Cancellation failed.")
        return 1


def cmd_open_orders(args: argparse.Namespace, client: BinanceFuturesClient, logger) -> int:
    """Handle the 'open-orders' sub-command."""
    symbol: Optional[str] = None
    if args.symbol:
        try:
            symbol = validate_symbol(args.symbol)
        except ValueError as exc:
            print_failure(f"Validation error: {exc}")
            return 1

    print_section("Open Orders" + (f" — {symbol}" if symbol else " — All Symbols"))
    try:
        orders = client.get_open_orders(symbol=symbol)
    except (BinanceAPIError, BinanceNetworkError) as exc:
        print_failure(str(exc))
        logger.error("Failed to fetch open orders: %s", exc)
        return 1

    if not orders:
        print("  (no open orders)")
        return 0

    for o in orders:
        print(
            f"  [{o.get('orderId')}] {o.get('symbol')} | "
            f"{o.get('side')} {o.get('type')} | "
            f"qty={o.get('origQty')} | price={o.get('price')} | "
            f"status={o.get('status')}"
        )
    return 0


def cmd_account(args: argparse.Namespace, client: BinanceFuturesClient, logger) -> int:
    """Handle the 'account' sub-command."""
    print_section("Account Summary")
    try:
        account = client.get_account()
    except (BinanceAPIError, BinanceNetworkError) as exc:
        print_failure(str(exc))
        logger.error("Failed to fetch account: %s", exc)
        return 1

    print(f"  Can Trade        : {account.get('canTrade')}")
    print(f"  Total Wallet Bal : {account.get('totalWalletBalance')} USDT")
    print(f"  Available Balance: {account.get('availableBalance')} USDT")
    print(f"  Total Unrealised : {account.get('totalUnrealizedProfit')} USDT")

    positions = [p for p in account.get("positions", []) if float(p.get("positionAmt", 0)) != 0]
    if positions:
        print(f"\n  Open Positions ({len(positions)}):")
        for p in positions:
            print(
                f"    {p['symbol']} | amt={p['positionAmt']} | "
                f"entry={p['entryPrice']} | pnl={p['unrealizedProfit']}"
            )
    else:
        print("\n  Open Positions : (none)")

    return 0


def cmd_ping(args: argparse.Namespace, client: BinanceFuturesClient, logger) -> int:
    """Handle the 'ping' sub-command."""
    print_section("Connectivity Test")
    try:
        client.ping()
        time_data = client.get_server_time()
        print(f"  Testnet reachable ✔")
        print(f"  Server time: {time_data.get('serverTime')} ms epoch")
        print_success("Ping successful.")
        return 0
    except (BinanceAPIError, BinanceNetworkError) as exc:
        print_failure(f"Ping failed: {exc}")
        return 1


# ──────────────────────────────────────────────────────────────────────────────
# Argument parser
# ──────────────────────────────────────────────────────────────────────────────

def cmd_twap(args: argparse.Namespace, client: BinanceFuturesClient, logger) -> int:
    """Handle the twap sub-command."""
    from decimal import Decimal as D
    try:
        total = validate_quantity(args.total_qty)
    except ValueError as e:
        print_failure(str(e)); return 1

    dry_run = getattr(args, "dry_run", False)
    manager = OrderManager(client)

    print_section("TWAP Execution" + (" [DRY RUN]" if dry_run else ""))
    print(f"  Symbol   : {args.symbol}  |  Side: {args.side}")
    print(f"  Total    : {total}  |  Slices: {args.slices}  |  Interval: {args.interval}s")

    def on_slice(i, sl):
        status = f"orderId={sl.result.order_id} qty={sl.result.executed_qty}" if sl.result.success else sl.result.error_message
        marker = "✔" if sl.result.success else "✗"
        print(f"  [{marker}] slice {i}/{args.slices} — {status}")

    executor = TwapExecutor(manager, dry_run=dry_run, on_slice=on_slice)
    result = executor.run(
        symbol=args.symbol, side=args.side,
        total_qty=D(str(total)), slices=args.slices,
        interval_seconds=args.interval,
    )
    print_section("TWAP Result")
    for line in result.summary_lines():
        print(line)
    if not result.aborted:
        print_success(f"TWAP done — executed {result.executed_qty} / {total}.")
    else:
        print_failure(f"TWAP aborted: {result.abort_reason}")
    for sl in result.completed_slices + result.failed_slices:
        record_order(sl.result, symbol=args.symbol, side=args.side, order_type="MARKET",
                     quantity=str(sl.quantity), strategy="twap", dry_run=dry_run)
    return 0 if not result.aborted else 1


def cmd_grid(args: argparse.Namespace, client: BinanceFuturesClient, logger) -> int:
    """Handle the grid sub-command."""
    from decimal import Decimal as D
    try:
        centre = validate_price(args.centre, "LIMIT")
        step   = validate_price(args.step,   "LIMIT")
        qty    = validate_quantity(args.qty)
    except ValueError as e:
        print_failure(str(e)); return 1

    dry_run = getattr(args, "dry_run", False)
    manager = OrderManager(client)

    print_section("Grid Trading" + (" [DRY RUN]" if dry_run else ""))
    print(f"  Symbol : {args.symbol}  centre={centre}  step={step}  levels={args.levels}  qty={qty}")

    executor = GridExecutor(manager, dry_run=dry_run)
    result = executor.run(
        symbol=args.symbol, centre_price=D(str(centre)), step=D(str(step)),
        levels=args.levels, qty_per_level=D(str(qty)),
    )
    print_section("Grid Result")
    for line in result.summary_lines():
        print(line)
    if result.failed_count == 0:
        print_success(f"All {result.placed_count} grid orders placed.")
    else:
        print_failure(f"{result.placed_count} placed, {result.failed_count} failed.")
    for lvl in result.grid_levels:
        if lvl.result:
            record_order(lvl.result, symbol=args.symbol, side=lvl.side, order_type="LIMIT",
                         quantity=str(lvl.quantity), price=str(lvl.price), strategy="grid",
                         dry_run=dry_run)
    return 0


def cmd_history(args: argparse.Namespace, client: BinanceFuturesClient, logger) -> int:
    """Handle the history sub-command."""
    rows  = fetch_history(symbol=args.symbol, limit=args.limit)
    stats = fetch_stats()

    print_section("Order History")
    if stats:
        print(f"  Total: {stats.get('total',0)}  |  OK: {stats.get('successful',0)}  |  Failed: {stats.get('failed',0)}  |  Dry: {stats.get('dry_runs',0)}")

    if not rows:
        print("  (no records)")
        return 0

    print(f"\n  {'#':<6} {'Time':<20} {'Symbol':<10} {'Side':<5} {'Type':<12} {'Qty':<10} {'Strat':<8} Status")
    print("  " + "─"*80)
    for r in rows:
        dr = " ~" if r["dry_run"] else ""
        er = " ✗" if r["error"] else ""
        print(f"  {r['id']:<6} {r['created_at'][:19]:<20} {r['symbol']:<10} "
              f"{r['side']:<5} {r['order_type']:<12} {r['quantity']:<10} "
              f"{r['strategy']:<8} {r['status'] or '—'}{dr}{er}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading_bot",
        description="Binance Futures Testnet — simple order-placement CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console log verbosity (default: INFO). File always captures DEBUG.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── order ──
    order_p = subparsers.add_parser("order", help="Place a new futures order.")
    order_p.add_argument("--symbol", required=True, help="Trading pair, e.g. BTCUSDT.")
    order_p.add_argument(
        "--side", required=True, choices=["BUY", "SELL"], type=str.upper, help="BUY or SELL."
    )
    order_p.add_argument(
        "--type",
        required=True,
        choices=["MARKET", "LIMIT", "STOP_MARKET"],
        type=str.upper,
        help="Order type.",
    )
    order_p.add_argument("--qty", required=True, help="Order quantity.")
    order_p.add_argument("--price", default=None, help="Limit price (required for LIMIT).")
    order_p.add_argument(
        "--stop-price", dest="stop_price", default=None,
        help="Stop trigger price (required for STOP_MARKET)."
    )
    order_p.add_argument(
        "--reduce-only", dest="reduce_only", action="store_true",
        help="Mark order as reduce-only."
    )
    order_p.add_argument("--dry-run", dest="dry_run", action="store_true",
                          help="Simulate order without hitting the API.")

    # ── twap ──
    twap_p = subparsers.add_parser("twap", help="Execute a TWAP order (time-sliced).")
    twap_p.add_argument("--symbol",    required=True, help="Trading pair.")
    twap_p.add_argument("--side",      required=True, choices=["BUY","SELL"], type=str.upper)
    twap_p.add_argument("--total-qty", required=True, dest="total_qty", help="Total quantity.")
    twap_p.add_argument("--slices",    required=True, type=int, help="Number of slices (>=2).")
    twap_p.add_argument("--interval",  required=True, type=int, help="Seconds between slices.")
    twap_p.add_argument("--dry-run",   dest="dry_run", action="store_true")

    # ── grid ──
    grid_p = subparsers.add_parser("grid", help="Place a grid of LIMIT orders.")
    grid_p.add_argument("--symbol",  required=True, help="Trading pair.")
    grid_p.add_argument("--centre",  required=True, help="Centre price.")
    grid_p.add_argument("--step",    required=True, help="Price step between levels.")
    grid_p.add_argument("--levels",  required=True, type=int, help="Levels per side.")
    grid_p.add_argument("--qty",     required=True, help="Quantity per level.")
    grid_p.add_argument("--dry-run", dest="dry_run", action="store_true")

    # ── history ──
    hist_p = subparsers.add_parser("history", help="Show local order history.")
    hist_p.add_argument("--symbol", default=None)
    hist_p.add_argument("--limit",  default=20, type=int)

    # ── cancel ──
    cancel_p = subparsers.add_parser("cancel", help="Cancel an open order.")
    cancel_p.add_argument("--symbol", required=True, help="Trading pair.")
    cancel_p.add_argument("--order-id", required=True, type=int, dest="order_id",
                           help="Binance order ID to cancel.")

    # ── open-orders ──
    oo_p = subparsers.add_parser("open-orders", help="List open orders.")
    oo_p.add_argument("--symbol", default=None, help="Filter by trading pair (optional).")

    # ── account ──
    subparsers.add_parser("account", help="Show account balances and positions.")

    # ── ping ──
    subparsers.add_parser("ping", help="Test connectivity to the testnet.")

    return parser


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(args.log_level)
    logger = get_logger("cli")

    print_banner()
    print(f"\n  Log file: {LOG_FILE}\n")

    logger.debug("CLI args: %s", vars(args))

    # Credentials
    api_key, api_secret = get_credentials()

    # Build shared client
    client = BinanceFuturesClient(api_key=api_key, api_secret=api_secret)

    # Dispatch
    dispatch = {
        "order":       cmd_order,
        "twap":        cmd_twap,
        "grid":        cmd_grid,
        "cancel":      cmd_cancel,
        "open-orders": cmd_open_orders,
        "account":     cmd_account,
        "history":     cmd_history,
        "ping":        cmd_ping,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        print_failure(f"Unknown command: {args.command}")
        sys.exit(1)

    exit_code = handler(args, client, logger)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
