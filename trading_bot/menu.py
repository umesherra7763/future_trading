#!/usr/bin/env python3
"""
Interactive menu-driven CLI for the Binance Futures Testnet Trading Bot.

Run with:
    python menu.py
    python menu.py --dry-run          # simulate without hitting the API
    python menu.py --log-level DEBUG
"""

from __future__ import annotations

import os
import sys
import time
from decimal import Decimal
from typing import Optional

from bot.client import BinanceFuturesClient, BinanceAPIError, BinanceNetworkError
from bot.config import load_config, write_sample_config, CONFIG_TOML
from bot.history import record_order, fetch_history, fetch_stats
from bot.logging_config import setup_logging, get_logger, LOG_FILE
from bot.orders import OrderManager, OrderResult
from bot.strategies.grid import GridExecutor
from bot.strategies.twap import TwapExecutor
from bot.validators import (
    validate_symbol, validate_side, validate_order_type,
    validate_quantity, validate_price, validate_stop_price,
)

logger = get_logger("menu")

# ──────────────────────────────────────────────────────────────────────────────
# Terminal colour helpers (gracefully degrades on Windows without colorama)
# ──────────────────────────────────────────────────────────────────────────────

try:
    import colorama
    colorama.init(autoreset=True)
    _C = {
        "reset":  colorama.Style.RESET_ALL,
        "bold":   colorama.Style.BRIGHT,
        "green":  colorama.Fore.GREEN,
        "red":    colorama.Fore.RED,
        "yellow": colorama.Fore.YELLOW,
        "cyan":   colorama.Fore.CYAN,
        "white":  colorama.Fore.WHITE,
        "dim":    colorama.Style.DIM,
    }
except ImportError:
    _C = {k: "" for k in ("reset","bold","green","red","yellow","cyan","white","dim")}


def c(text: str, *styles: str) -> str:
    return "".join(_C.get(s, "") for s in styles) + text + _C["reset"]


# ──────────────────────────────────────────────────────────────────────────────
# Display helpers
# ──────────────────────────────────────────────────────────────────────────────

BANNER = c("""
╔══════════════════════════════════════════════════════════════╗
║        Binance Futures Testnet — Interactive Trading Bot     ║
╚══════════════════════════════════════════════════════════════╝""", "cyan", "bold")

SEP   = c("─" * 62, "dim")
SEP_S = c("·" * 62, "dim")


def cls() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def header(title: str) -> None:
    print(f"\n{SEP}")
    print(c(f"  {title}", "bold", "cyan"))
    print(SEP)


def ok(msg: str) -> None:
    print(c(f"\n  ✔  {msg}", "green", "bold"))


def err(msg: str) -> None:
    print(c(f"\n  ✗  {msg}", "red", "bold"))


def warn(msg: str) -> None:
    print(c(f"\n  ⚠  {msg}", "yellow"))


def info(msg: str) -> None:
    print(c(f"  ℹ  {msg}", "cyan"))


def dry_run_badge() -> str:
    return c(" [DRY RUN] ", "yellow", "bold")


# ──────────────────────────────────────────────────────────────────────────────
# Input helpers — prompt + validate in a loop
# ──────────────────────────────────────────────────────────────────────────────

def prompt(label: str, default: Optional[str] = None, secret: bool = False) -> str:
    """Show a prompt, return stripped input. Ctrl-C raises KeyboardInterrupt."""
    hint = f" [{c(default, 'yellow')}]" if default else ""
    display = label + hint + c(" › ", "dim")
    if secret:
        import getpass
        return getpass.getpass(display) or (default or "")
    raw = input(display)
    return raw.strip() or (default or "")


def prompt_validated(label: str, validator, default: Optional[str] = None) -> object:
    """Prompt in a loop until validator(value) succeeds. Returns validated result."""
    while True:
        raw = prompt(label, default)
        try:
            return validator(raw)
        except ValueError as e:
            err(str(e))


def prompt_yes_no(question: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = prompt(f"{question} ({hint})").lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def prompt_int(label: str, min_val: int = 1, max_val: int = 9999,
               default: Optional[int] = None) -> int:
    while True:
        raw = prompt(label, str(default) if default is not None else None)
        try:
            val = int(raw)
        except ValueError:
            err(f"Please enter a whole number between {min_val} and {max_val}.")
            continue
        if not (min_val <= val <= max_val):
            err(f"Value must be between {min_val} and {max_val}.")
            continue
        return val


# ──────────────────────────────────────────────────────────────────────────────
# Order-result printer
# ──────────────────────────────────────────────────────────────────────────────

def print_result(result: OrderResult) -> None:
    header("Order Response")
    for line in result.summary_lines():
        if "✗" in line:
            print(c(line, "red"))
        else:
            print(line)
    if result.success:
        ok("Order placed successfully.")
    else:
        err("Order placement failed.")


# ──────────────────────────────────────────────────────────────────────────────
# Shared order-parameter collection
# ──────────────────────────────────────────────────────────────────────────────

def collect_order_params(default_symbol: str = "BTCUSDT") -> dict:
    """Interactively collect and validate order parameters."""
    symbol   = prompt_validated("Symbol (e.g. BTCUSDT)", validate_symbol, default_symbol)
    side_raw = prompt_validated("Side  [BUY/SELL]",      validate_side)
    type_raw = prompt_validated(
        "Type  [MARKET/LIMIT/STOP_MARKET]",
        validate_order_type,
    )
    qty = prompt_validated("Quantity", validate_quantity)

    price = None
    stop_price = None

    if type_raw == "LIMIT":
        price = prompt_validated("Limit Price", lambda v: validate_price(v, "LIMIT"))

    if type_raw == "STOP_MARKET":
        stop_price = prompt_validated("Stop Price", lambda v: validate_stop_price(v, "STOP_MARKET"))

    return {
        "symbol":     str(symbol),
        "side":       str(side_raw),
        "order_type": str(type_raw),
        "quantity":   Decimal(str(qty)),
        "price":      Decimal(str(price)) if price else None,
        "stop_price": Decimal(str(stop_price)) if stop_price else None,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Menu actions
# ──────────────────────────────────────────────────────────────────────────────

def action_place_order(mgr: OrderManager, cfg, dry_run: bool) -> None:
    header("Place Order" + (dry_run_badge() if dry_run else ""))
    params = collect_order_params(cfg.default_symbol)

    # Summary before confirming
    header("Order Summary")
    print(f"  Symbol     : {c(params['symbol'], 'bold')}")
    print(f"  Side       : {c(params['side'], 'green' if params['side']=='BUY' else 'red', 'bold')}")
    print(f"  Type       : {params['order_type']}")
    print(f"  Quantity   : {params['quantity']}")
    if params["price"]:
        print(f"  Price      : {params['price']}")
    if params["stop_price"]:
        print(f"  Stop Price : {params['stop_price']}")

    if not prompt_yes_no("\nConfirm order?", default=True):
        warn("Order cancelled.")
        return

    if dry_run:
        result = OrderResult(
            success=True,
            order_id=999000001,
            symbol=params["symbol"],
            side=params["side"],
            order_type=params["order_type"],
            status="FILLED (simulated)",
            orig_qty=str(params["quantity"]),
            executed_qty=str(params["quantity"]),
            avg_price="0 (dry run)",
        )
    else:
        result = mgr.place_order(**params)

    print_result(result)
    record_order(
        result, strategy="manual", dry_run=dry_run,
        **{k: str(v) if v is not None else None
           for k, v in params.items() if k != "order_type"},
        order_type=params["order_type"],
    )


def action_twap(mgr: OrderManager, cfg, dry_run: bool) -> None:
    header("TWAP Execution" + (dry_run_badge() if dry_run else ""))
    info("Splits a large order into N equal slices placed at fixed intervals.")
    print()

    symbol = prompt_validated("Symbol", validate_symbol, cfg.default_symbol)
    side   = prompt_validated("Side [BUY/SELL]", validate_side)
    total  = prompt_validated("Total Quantity", validate_quantity)
    slices = prompt_int("Number of slices", 2, 100, 5)
    ivl    = prompt_int("Interval between slices (seconds)", 1, 3600, 60)
    otype  = "MARKET"

    header("TWAP Summary" + (dry_run_badge() if dry_run else ""))
    print(f"  Symbol      : {symbol}")
    print(f"  Side        : {side}")
    print(f"  Total qty   : {total}")
    print(f"  Slices      : {slices}  × {Decimal(str(total)) / slices:.8f} each")
    print(f"  Interval    : {ivl}s  (total ~{slices * ivl}s)")
    print(f"  Order type  : {otype}")
    if dry_run:
        print(c("  Mode        : DRY RUN — no real orders", "yellow"))

    if not prompt_yes_no("\nStart TWAP?", default=True):
        warn("TWAP cancelled.")
        return

    executor = TwapExecutor(
        mgr,
        dry_run=dry_run,
        on_slice=lambda i, sl: print(
            c(f"\n  [slice {i}/{slices}] ", "cyan")
            + (c("✔ " + str(sl.result.status), "green") if sl.result.success
               else c("✗ " + str(sl.result.error_message), "red"))
        ),
    )

    result = executor.run(
        symbol=str(symbol),
        side=str(side),
        total_qty=Decimal(str(total)),
        slices=slices,
        interval_seconds=ivl,
        order_type=otype,
    )

    header("TWAP Result")
    for line in result.summary_lines():
        print(line)

    if not result.aborted:
        ok(f"TWAP complete — executed {result.executed_qty} of {total}.")
    else:
        err(f"TWAP aborted: {result.abort_reason}")

    # Record each slice in history
    for sl in result.completed_slices + result.failed_slices:
        record_order(
            sl.result,
            symbol=str(symbol),
            side=str(side),
            order_type=otype,
            quantity=str(sl.quantity),
            strategy="twap",
            dry_run=dry_run,
        )


def action_grid(mgr: OrderManager, cfg, dry_run: bool) -> None:
    header("Grid Trading" + (dry_run_badge() if dry_run else ""))
    info("Places symmetric BUY/SELL LIMIT orders around a centre price.")
    print()

    symbol  = prompt_validated("Symbol", validate_symbol, cfg.default_symbol)
    centre  = prompt_validated("Centre price", lambda v: validate_price(v, "LIMIT"))
    step    = prompt_validated("Step between levels (price)", lambda v: validate_price(v, "LIMIT"))
    levels  = prompt_int("Levels (each side)", 1, 20, 3)
    qty_lvl = prompt_validated("Quantity per level", validate_quantity)

    executor = GridExecutor(mgr, dry_run=dry_run)
    preview  = executor.build_levels(
        centre_price=Decimal(str(centre)),
        step=Decimal(str(step)),
        levels=levels,
        qty_per_level=Decimal(str(qty_lvl)),
    )

    header("Grid Preview" + (dry_run_badge() if dry_run else ""))
    for lvl in preview:
        side_col = "green" if lvl.side == "BUY" else "red"
        print(f"  {c(lvl.side, side_col):<6}  @ {c(str(lvl.price), 'bold'):>14}  qty={lvl.quantity}")

    if not prompt_yes_no(f"\nPlace {len(preview)} orders?", default=True):
        warn("Grid cancelled.")
        return

    result = executor.run(
        symbol=str(symbol),
        centre_price=Decimal(str(centre)),
        step=Decimal(str(step)),
        levels=levels,
        qty_per_level=Decimal(str(qty_lvl)),
    )

    header("Grid Result")
    for line in result.summary_lines():
        print(line)

    if result.failed_count == 0:
        ok(f"All {result.placed_count} grid orders placed.")
    else:
        warn(f"{result.placed_count} placed, {result.failed_count} failed.")

    for lvl in result.grid_levels:
        if lvl.result:
            record_order(
                lvl.result,
                symbol=str(symbol),
                side=lvl.side,
                order_type="LIMIT",
                quantity=str(lvl.quantity),
                price=str(lvl.price),
                strategy="grid",
                dry_run=dry_run,
            )


def action_open_orders(client: BinanceFuturesClient, cfg) -> None:
    header("Open Orders")
    sym_raw = prompt("Filter by symbol (leave blank for all)", "").strip()
    symbol  = sym_raw.upper() if sym_raw else None

    try:
        orders = client.get_open_orders(symbol=symbol)
    except (BinanceAPIError, BinanceNetworkError) as e:
        err(str(e))
        return

    if not orders:
        info("No open orders.")
        return

    print(f"\n  {'ID':<14} {'Symbol':<10} {'Side':<5} {'Type':<12} {'Price':<12} {'Qty':<10} Status")
    print(SEP_S)
    for o in orders:
        side_col = "green" if o.get("side") == "BUY" else "red"
        print(
            f"  {o.get('orderId', ''):<14} "
            f"{o.get('symbol', ''):<10} "
            f"{c(o.get('side',''), side_col):<5}  "
            f"{o.get('type',''):<12} "
            f"{o.get('price',''):<12} "
            f"{o.get('origQty',''):<10} "
            f"{o.get('status','')}"
        )


def action_cancel(mgr: OrderManager, cfg) -> None:
    header("Cancel Order")
    symbol   = prompt_validated("Symbol", validate_symbol, cfg.default_symbol)
    order_id = prompt_int("Order ID", 1, 9_999_999_999)

    result = mgr.cancel_order(symbol=str(symbol), order_id=order_id)
    header("Cancel Response")
    for line in result.summary_lines():
        print(line)
    if result.success:
        ok("Order cancelled.")
    else:
        err("Cancellation failed.")


def action_pnl(client: BinanceFuturesClient) -> None:
    header("Live Positions & P&L")
    try:
        account = client.get_account()
    except (BinanceAPIError, BinanceNetworkError) as e:
        err(str(e))
        return

    print(f"\n  Wallet Balance    : {c(account.get('totalWalletBalance','?'), 'bold')} USDT")
    print(f"  Available Balance : {account.get('availableBalance','?')} USDT")
    unreal = float(account.get("totalUnrealizedProfit", 0))
    color = "green" if unreal >= 0 else "red"
    print(f"  Unrealised P&L    : {c(f'{unreal:+.4f}', color)} USDT")

    positions = [p for p in account.get("positions", [])
                 if float(p.get("positionAmt", 0)) != 0]

    if not positions:
        print(f"\n  {c('No open positions.', 'dim')}")
        return

    print(f"\n  {'Symbol':<12} {'Amt':>10} {'Entry':>12} {'Mark':>12} {'uPnL':>12}")
    print(SEP_S)
    for p in positions:
        pnl = float(p.get("unrealizedProfit", 0))
        pnl_col = "green" if pnl >= 0 else "red"
        print(
            f"  {p['symbol']:<12}"
            f" {p['positionAmt']:>10}"
            f" {p['entryPrice']:>12}"
            f" {p.get('markPrice', '—'):>12}"
            f" {c(f'{pnl:+.4f}', pnl_col):>12}"
        )


def action_history() -> None:
    header("Order History")
    sym_raw = prompt("Filter by symbol (blank = all)", "").strip()
    symbol  = sym_raw.upper() if sym_raw else None
    limit   = prompt_int("Max rows to show", 1, 200, 20)

    rows = fetch_history(symbol=symbol, limit=limit)
    stats = fetch_stats()

    if stats:
        print(f"\n  Total recorded  : {stats.get('total', 0)}")
        print(f"  Successful      : {c(str(stats.get('successful', 0)), 'green')}")
        print(f"  Failed          : {c(str(stats.get('failed', 0)), 'red')}")
        print(f"  Symbols traded  : {stats.get('symbols_traded', 0)}")

    if not rows:
        print(f"\n  {c('No history found.', 'dim')}")
        return

    print(f"\n  {'#':<6} {'Time':<20} {'Symbol':<10} {'Side':<5} {'Type':<12} {'Qty':<10} {'Status':<16} {'Strat'}")
    print(SEP_S)
    for r in rows:
        side_col = "green" if r["side"] == "BUY" else "red"
        err_marker = c(" ✗", "red") if r["error"] else ""
        dr_marker  = c(" ~", "yellow") if r["dry_run"] else ""
        print(
            f"  {r['id']:<6} "
            f"{r['created_at'][:19]:<20} "
            f"{r['symbol']:<10} "
            f"{c(r['side'], side_col):<5}  "
            f"{r['order_type']:<12} "
            f"{r['quantity']:<10} "
            f"{(r['status'] or '—'):<16} "
            f"{r['strategy']}{err_marker}{dr_marker}"
        )


def action_ping(client: BinanceFuturesClient) -> None:
    header("Connectivity Test")
    try:
        client.ping()
        td = client.get_server_time()
        ok(f"Testnet reachable | server time: {td.get('serverTime')} ms")
    except (BinanceAPIError, BinanceNetworkError) as e:
        err(str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Main menu loop
# ──────────────────────────────────────────────────────────────────────────────

MENU_ITEMS = [
    ("1", "Place Order           (MARKET / LIMIT / STOP_MARKET)"),
    ("2", "TWAP Execution        (time-sliced large order)"),
    ("3", "Grid Trading          (ladder buy + sell limits)"),
    ("4", "Open Orders           (view & manage)"),
    ("5", "Cancel Order"),
    ("6", "Live Positions & P&L"),
    ("7", "Order History         (local SQLite log)"),
    ("8", "Connectivity Ping"),
    ("q", "Quit"),
]


def show_menu(dry_run: bool) -> None:
    print(BANNER)
    if dry_run:
        print(dry_run_badge() + c(" All orders are simulated — no API calls to the exchange.", "yellow"))
    print(c(f"\n  Log: {LOG_FILE}", "dim"))
    print()
    for key, label in MENU_ITEMS:
        print(f"  {c(f'[{key}]', 'cyan', 'bold')}  {label}")
    print()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Interactive Binance Futures Testnet Trading Bot")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without placing real orders.")
    parser.add_argument("--log-level", default=None, choices=["DEBUG","INFO","WARNING","ERROR"])
    parser.add_argument("--config", default=None, help="Path to config.toml (default: ./config.toml)")
    args = parser.parse_args()

    # Load config (file + env)
    cfg = load_config(config_path=args.config)

    # CLI flags override config
    dry_run   = args.dry_run or cfg.dry_run
    log_level = args.log_level or cfg.log_level

    setup_logging(log_level)

    # Write sample config if absent
    write_sample_config(CONFIG_TOML)

    # Credentials
    if not cfg.api_key or not cfg.api_secret:
        err(
            "API credentials not found.\n"
            "  Set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET\n"
            "  OR add them to config.toml."
        )
        if not dry_run:
            sys.exit(1)
        warn("Continuing in dry-run mode with placeholder credentials.")
        cfg.api_key    = cfg.api_key    or "DRY_RUN_KEY"
        cfg.api_secret = cfg.api_secret or "DRY_RUN_SECRET"

    client  = BinanceFuturesClient(api_key=cfg.api_key, api_secret=cfg.api_secret, base_url=cfg.base_url)
    manager = OrderManager(client)

    logger.info("Interactive session started | dry_run=%s log_level=%s", dry_run, log_level)

    while True:
        try:
            cls()
            show_menu(dry_run)
            choice = prompt("Choice").lower()

            if choice == "q":
                print(c("\n  Goodbye!\n", "cyan"))
                logger.info("Interactive session ended.")
                break
            elif choice == "1":
                action_place_order(manager, cfg, dry_run)
            elif choice == "2":
                action_twap(manager, cfg, dry_run)
            elif choice == "3":
                action_grid(manager, cfg, dry_run)
            elif choice == "4":
                action_open_orders(client, cfg)
            elif choice == "5":
                action_cancel(manager, cfg)
            elif choice == "6":
                action_pnl(client)
            elif choice == "7":
                action_history()
            elif choice == "8":
                action_ping(client)
            else:
                warn(f"Unknown option '{choice}'. Please choose from the menu.")

            input(c("\n  Press Enter to continue…", "dim"))

        except KeyboardInterrupt:
            print(c("\n\n  Interrupted. Returning to menu…\n", "yellow"))
            time.sleep(0.5)
            continue


if __name__ == "__main__":
    main()
