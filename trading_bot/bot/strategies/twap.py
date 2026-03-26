"""
TWAP (Time-Weighted Average Price) order execution strategy.

Splits a large order into N equal slices and places them at fixed intervals
over a specified duration, reducing market impact.

Usage (programmatic)::

    from bot.strategies.twap import TwapExecutor
    executor = TwapExecutor(order_manager, client)
    result = executor.run(
        symbol="BTCUSDT", side="BUY", total_qty=Decimal("0.01"),
        slices=5, interval_seconds=60, order_type="MARKET"
    )
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Optional, Callable

from bot.logging_config import get_logger
from bot.orders import OrderManager, OrderResult

logger = get_logger("bot.strategies.twap")


@dataclass
class TwapSlice:
    """Result of a single TWAP slice execution."""
    slice_number: int
    quantity: Decimal
    result: OrderResult
    timestamp: float = field(default_factory=time.time)


@dataclass
class TwapResult:
    """Aggregated result of an entire TWAP execution."""
    symbol: str
    side: str
    total_qty: Decimal
    slices: int
    interval_seconds: int
    completed_slices: list[TwapSlice] = field(default_factory=list)
    failed_slices: list[TwapSlice] = field(default_factory=list)
    aborted: bool = False
    abort_reason: Optional[str] = None

    @property
    def executed_qty(self) -> Decimal:
        return sum(
            Decimal(s.result.executed_qty or "0")
            for s in self.completed_slices
            if s.result.success
        )

    @property
    def success_rate(self) -> float:
        total = len(self.completed_slices) + len(self.failed_slices)
        return len(self.completed_slices) / total if total > 0 else 0.0

    def summary_lines(self) -> list[str]:
        lines = [
            f"  Symbol          : {self.symbol}",
            f"  Side            : {self.side}",
            f"  Total Qty       : {self.total_qty}",
            f"  Slices planned  : {self.slices}",
            f"  Interval        : {self.interval_seconds}s",
            f"  Completed       : {len(self.completed_slices)} / {self.slices}",
            f"  Failed          : {len(self.failed_slices)}",
            f"  Executed Qty    : {self.executed_qty}",
            f"  Success Rate    : {self.success_rate:.0%}",
        ]
        if self.aborted:
            lines.append(f"  ⚠  Aborted: {self.abort_reason}")
        return lines


class TwapExecutor:
    """
    Executes a TWAP strategy by slicing a parent order over time.

    Args:
        order_manager: OrderManager instance for placing individual orders.
        dry_run:       If True, simulate without placing real orders.
        on_slice:      Optional callback invoked after each slice with (slice_num, TwapSlice).
    """

    def __init__(
        self,
        order_manager: OrderManager,
        dry_run: bool = False,
        on_slice: Optional[Callable[[int, TwapSlice], None]] = None,
    ) -> None:
        self._mgr = order_manager
        self._dry_run = dry_run
        self._on_slice = on_slice

    def run(
        self,
        symbol: str,
        side: str,
        total_qty: Decimal,
        slices: int,
        interval_seconds: int,
        order_type: str = "MARKET",
        price: Optional[Decimal] = None,
        abort_on_failure: bool = False,
    ) -> TwapResult:
        """
        Execute the TWAP strategy.

        Args:
            symbol:            Trading pair (e.g. 'BTCUSDT').
            side:              'BUY' or 'SELL'.
            total_qty:         Total quantity to execute.
            slices:            Number of equal slices.
            interval_seconds:  Seconds to wait between consecutive slices.
            order_type:        'MARKET' or 'LIMIT' for each slice.
            price:             Limit price (only for LIMIT slice orders).
            abort_on_failure:  Stop the TWAP if any slice fails.

        Returns:
            TwapResult with per-slice detail and aggregated statistics.
        """
        if slices < 2:
            raise ValueError("TWAP requires at least 2 slices.")
        if interval_seconds < 1 and not self._dry_run:
            raise ValueError("Interval must be at least 1 second.")

        slice_qty = (total_qty / slices).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        # Give any remainder to the last slice to avoid dust
        last_slice_qty = total_qty - slice_qty * (slices - 1)

        result = TwapResult(
            symbol=symbol,
            side=side,
            total_qty=total_qty,
            slices=slices,
            interval_seconds=interval_seconds,
        )

        logger.info(
            "TWAP start | %s %s total=%s slices=%d interval=%ds slice_qty=%s%s",
            side, symbol, total_qty, slices, interval_seconds, slice_qty,
            " [DRY RUN]" if self._dry_run else "",
        )

        for i in range(1, slices + 1):
            qty = last_slice_qty if i == slices else slice_qty

            logger.info("TWAP slice %d/%d | qty=%s", i, slices, qty)

            if self._dry_run:
                order_result = OrderResult(
                    success=True,
                    order_id=900000000 + i,
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    status="FILLED (simulated)",
                    orig_qty=str(qty),
                    executed_qty=str(qty),
                    avg_price="0 (dry run)",
                )
            else:
                order_result = self._mgr.place_order(
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    quantity=qty,
                    price=price,
                )

            ts = TwapSlice(slice_number=i, quantity=qty, result=order_result)

            if order_result.success:
                result.completed_slices.append(ts)
                logger.info(
                    "TWAP slice %d/%d SUCCESS | orderId=%s executedQty=%s",
                    i, slices, order_result.order_id, order_result.executed_qty,
                )
            else:
                result.failed_slices.append(ts)
                logger.error(
                    "TWAP slice %d/%d FAILED | %s", i, slices, order_result.error_message
                )
                if abort_on_failure:
                    result.aborted = True
                    result.abort_reason = f"Slice {i} failed: {order_result.error_message}"
                    logger.warning("TWAP aborted after slice %d failure.", i)
                    break

            if self._on_slice:
                self._on_slice(i, ts)

            # Wait before next slice (skip delay after the final slice)
            if i < slices:
                logger.debug("TWAP waiting %ds before slice %d…", interval_seconds, i + 1)
                time.sleep(interval_seconds)

        logger.info(
            "TWAP complete | executed=%s/%s success_rate=%.0f%%",
            result.executed_qty, total_qty, result.success_rate * 100,
        )
        return result
