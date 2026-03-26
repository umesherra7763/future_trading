"""
Local SQLite order history tracker.

Every order placed through the bot (or simulated) is persisted here for
audit, reporting, and P&L review — independent of Binance's own records.

Schema
------
orders(
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      INTEGER,               -- Binance orderId (NULL for dry-runs)
    symbol        TEXT    NOT NULL,
    side          TEXT    NOT NULL,
    order_type    TEXT    NOT NULL,
    strategy      TEXT    DEFAULT 'manual',
    quantity      TEXT    NOT NULL,
    price         TEXT,
    stop_price    TEXT,
    executed_qty  TEXT,
    avg_price     TEXT,
    status        TEXT,
    dry_run       INTEGER DEFAULT 0,    -- boolean
    error         TEXT,                 -- NULL on success
    created_at    TEXT    NOT NULL      -- ISO-8601 UTC
)
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Iterator

from bot.logging_config import get_logger
from bot.orders import OrderResult

logger = get_logger("bot.history")

DB_PATH = Path(__file__).parent.parent / "logs" / "orders.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     INTEGER,
    symbol       TEXT    NOT NULL,
    side         TEXT    NOT NULL,
    order_type   TEXT    NOT NULL,
    strategy     TEXT    NOT NULL DEFAULT 'manual',
    quantity     TEXT    NOT NULL,
    price        TEXT,
    stop_price   TEXT,
    executed_qty TEXT,
    avg_price    TEXT,
    status       TEXT,
    dry_run      INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    created_at   TEXT    NOT NULL
);
"""


@contextmanager
def _connect(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(_CREATE_TABLE)
        conn.commit()
        yield conn
    finally:
        conn.close()


def record_order(
    result: OrderResult,
    symbol: str,
    side: str,
    order_type: str,
    quantity: str,
    price: Optional[str] = None,
    stop_price: Optional[str] = None,
    strategy: str = "manual",
    dry_run: bool = False,
    db_path: Path = DB_PATH,
) -> int:
    """
    Persist an OrderResult to the local database.

    Args:
        result:     OrderResult returned by OrderManager (success or failure).
        symbol:     Trading pair.
        side:       'BUY' or 'SELL'.
        order_type: 'MARKET', 'LIMIT', etc.
        quantity:   Quantity string as submitted.
        price:      Limit price string (may be None).
        stop_price: Stop price string (may be None).
        strategy:   'manual', 'twap', 'grid', etc.
        dry_run:    True if this was a simulation.
        db_path:    Override DB location (useful in tests).

    Returns:
        The auto-assigned row id.
    """
    now = datetime.now(timezone.utc).isoformat()

    row = {
        "order_id":     result.order_id,
        "symbol":       symbol,
        "side":         side,
        "order_type":   order_type,
        "strategy":     strategy,
        "quantity":     quantity,
        "price":        price,
        "stop_price":   stop_price,
        "executed_qty": result.executed_qty,
        "avg_price":    result.avg_price,
        "status":       result.status,
        "dry_run":      1 if dry_run else 0,
        "error":        result.error_message if not result.success else None,
        "created_at":   now,
    }

    sql = """
        INSERT INTO orders
            (order_id, symbol, side, order_type, strategy, quantity, price,
             stop_price, executed_qty, avg_price, status, dry_run, error, created_at)
        VALUES
            (:order_id, :symbol, :side, :order_type, :strategy, :quantity, :price,
             :stop_price, :executed_qty, :avg_price, :status, :dry_run, :error, :created_at)
    """
    with _connect(db_path) as conn:
        cur = conn.execute(sql, row)
        conn.commit()
        row_id = cur.lastrowid

    logger.debug(
        "Order recorded | rowId=%d orderId=%s symbol=%s status=%s",
        row_id, result.order_id, symbol, result.status,
    )
    return row_id


def fetch_history(
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    limit: int = 50,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Retrieve order history rows, newest first.

    Args:
        symbol:   Filter by trading pair (optional).
        strategy: Filter by strategy name (optional).
        limit:    Maximum rows to return.

    Returns:
        List of dicts with order fields.
    """
    conditions = []
    params: list = []

    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol.upper())
    if strategy:
        conditions.append("strategy = ?")
        params.append(strategy)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM orders {where} ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return [dict(r) for r in rows]


def fetch_stats(
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> dict:
    """
    Return aggregate statistics, optionally filtered by symbol and/or strategy.
    """
    conditions = []
    params: list = []
    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol.upper())
    if strategy:
        conditions.append("strategy = ?")
        params.append(strategy)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT
            COUNT(*)                                       AS total,
            SUM(CASE WHEN error IS NULL THEN 1 ELSE 0 END)     AS successful,
            SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN dry_run = 1 THEN 1 ELSE 0 END)      AS dry_runs,
            COUNT(DISTINCT symbol)                             AS symbols_traded
        FROM orders {where}
    """
    with _connect(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}
