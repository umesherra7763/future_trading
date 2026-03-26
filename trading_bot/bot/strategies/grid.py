"""
Grid trading strategy.

Places a symmetric ladder of LIMIT orders around a centre price:
  - N buy orders spaced at `step` intervals below centre
  - N sell orders spaced at `step` intervals above centre

This captures profit from price oscillating within the grid range.

Usage::

    from bot.strategies.grid import GridExecutor
    executor = GridExecutor(order_manager)
    result = executor.run(
        symbol="BTCUSDT",
        centre_price=Decimal("68000"),
        step=Decimal("500"),
        levels=3,
        qty_per_level=Decimal("0.001"),
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from bot.logging_config import get_logger
from bot.orders import OrderManager, OrderResult

logger = get_logger("bot.strategies.grid")


@dataclass
class GridLevel:
    """A single price level in the grid."""
    side: str          # 'BUY' or 'SELL'
    price: Decimal
    quantity: Decimal
    result: Optional[OrderResult] = None

    @property
    def placed(self) -> bool:
        return self.result is not None and self.result.success


@dataclass
class GridResult:
    """Aggregated result of a grid placement run."""
    symbol: str
    centre_price: Decimal
    step: Decimal
    levels: int
    qty_per_level: Decimal
    grid_levels: list[GridLevel] = field(default_factory=list)

    @property
    def placed_count(self) -> int:
        return sum(1 for g in self.grid_levels if g.placed)

    @property
    def failed_count(self) -> int:
        return sum(1 for g in self.grid_levels if g.result and not g.result.success)

    def summary_lines(self) -> list[str]:
        lines = [
            f"  Symbol        : {self.symbol}",
            f"  Centre Price  : {self.centre_price}",
            f"  Step Size     : {self.step}",
            f"  Levels (each) : {self.levels} buy + {self.levels} sell",
            f"  Qty/Level     : {self.qty_per_level}",
            f"  Orders placed : {self.placed_count} / {len(self.grid_levels)}",
            f"  Failed        : {self.failed_count}",
            "",
            "  Grid layout (top → bottom):",
        ]
        for lvl in sorted(self.grid_levels, key=lambda g: g.price, reverse=True):
            status = "✔" if lvl.placed else "✗"
            oid = lvl.result.order_id if lvl.result and lvl.result.success else "—"
            lines.append(
                f"    [{status}] {lvl.side:<4} @ {lvl.price:>12}  qty={lvl.quantity}  id={oid}"
            )
        return lines


class GridExecutor:
    """
    Places a symmetric grid of LIMIT orders around a centre price.

    Args:
        order_manager: OrderManager instance.
        dry_run:       Simulate without placing real orders.
    """

    def __init__(self, order_manager: OrderManager, dry_run: bool = False) -> None:
        self._mgr = order_manager
        self._dry_run = dry_run

    def build_levels(
        self,
        centre_price: Decimal,
        step: Decimal,
        levels: int,
        qty_per_level: Decimal,
    ) -> list[GridLevel]:
        """
        Compute grid price levels without placing orders.

        Returns a list sorted high → low: sells first, then buys.
        """
        grid: list[GridLevel] = []

        for i in range(1, levels + 1):
            sell_price = centre_price + step * i
            buy_price = centre_price - step * i
            grid.append(GridLevel(side="SELL", price=sell_price, quantity=qty_per_level))
            grid.append(GridLevel(side="BUY", price=buy_price, quantity=qty_per_level))

        return sorted(grid, key=lambda g: g.price, reverse=True)

    def run(
        self,
        symbol: str,
        centre_price: Decimal,
        step: Decimal,
        levels: int,
        qty_per_level: Decimal,
        abort_on_failure: bool = False,
    ) -> GridResult:
        """
        Place all grid LIMIT orders.

        Args:
            symbol:          Trading pair.
            centre_price:    Mid-price around which the grid is centred.
            step:            Price distance between adjacent levels.
            levels:          Number of buy levels AND sell levels (e.g. 3 = 6 total orders).
            qty_per_level:   Order quantity for each grid level.
            abort_on_failure: Stop placing remaining orders if one fails.

        Returns:
            GridResult with per-level detail.
        """
        if levels < 1:
            raise ValueError("Grid requires at least 1 level.")
        if step <= 0:
            raise ValueError("Step must be positive.")
        if centre_price <= 0:
            raise ValueError("Centre price must be positive.")

        grid_levels = self.build_levels(centre_price, step, levels, qty_per_level)

        result = GridResult(
            symbol=symbol,
            centre_price=centre_price,
            step=step,
            levels=levels,
            qty_per_level=qty_per_level,
            grid_levels=grid_levels,
        )

        logger.info(
            "Grid start | %s centre=%s step=%s levels=%d qty_each=%s total_orders=%d%s",
            symbol, centre_price, step, levels, qty_per_level, len(grid_levels),
            " [DRY RUN]" if self._dry_run else "",
        )

        for lvl in grid_levels:
            logger.info(
                "Placing grid order | %s %s @ %s qty=%s",
                lvl.side, symbol, lvl.price, lvl.quantity,
            )

            if self._dry_run:
                lvl.result = OrderResult(
                    success=True,
                    order_id=800000000 + int(lvl.price),
                    symbol=symbol,
                    side=lvl.side,
                    order_type="LIMIT",
                    status="NEW (simulated)",
                    orig_qty=str(lvl.quantity),
                    executed_qty="0",
                    price=str(lvl.price),
                )
            else:
                lvl.result = self._mgr.place_order(
                    symbol=symbol,
                    side=lvl.side,
                    order_type="LIMIT",
                    quantity=lvl.quantity,
                    price=lvl.price,
                )

            if lvl.result.success:
                logger.info(
                    "Grid order placed | %s @ %s orderId=%s",
                    lvl.side, lvl.price, lvl.result.order_id,
                )
            else:
                logger.error(
                    "Grid order FAILED | %s @ %s error=%s",
                    lvl.side, lvl.price, lvl.result.error_message,
                )
                if abort_on_failure:
                    logger.warning("Grid placement aborted after failure.")
                    break

        logger.info(
            "Grid complete | placed=%d/%d failed=%d",
            result.placed_count, len(grid_levels), result.failed_count,
        )
        return result
