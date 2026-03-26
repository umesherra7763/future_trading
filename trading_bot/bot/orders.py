"""
High-level order placement logic.

This module owns the business logic for placing orders:
  - Accepts already-validated Python types from the CLI layer.
  - Delegates raw HTTP to BinanceFuturesClient.
  - Formats and returns a human-readable OrderResult.
  - Logs order lifecycle at INFO / ERROR level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from bot.client import BinanceFuturesClient, BinanceAPIError, BinanceNetworkError
from bot.logging_config import get_logger

logger = get_logger("bot.orders")


@dataclass
class OrderResult:
    """
    Normalised representation of a Binance order response.

    Provides a clean surface for the CLI to print without knowing the raw
    Binance JSON structure.
    """

    success: bool
    order_id: Optional[int] = None
    client_order_id: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[str] = None
    order_type: Optional[str] = None
    status: Optional[str] = None
    orig_qty: Optional[str] = None
    executed_qty: Optional[str] = None
    avg_price: Optional[str] = None
    price: Optional[str] = None
    stop_price: Optional[str] = None
    time_in_force: Optional[str] = None
    update_time: Optional[int] = None
    raw: dict = field(default_factory=dict)
    error_message: Optional[str] = None

    @classmethod
    def from_response(cls, data: dict) -> "OrderResult":
        """Build an OrderResult from a successful Binance order response dict."""
        return cls(
            success=True,
            order_id=data.get("orderId"),
            client_order_id=data.get("clientOrderId"),
            symbol=data.get("symbol"),
            side=data.get("side"),
            order_type=data.get("type"),
            status=data.get("status"),
            orig_qty=data.get("origQty"),
            executed_qty=data.get("executedQty"),
            avg_price=data.get("avgPrice"),
            price=data.get("price"),
            stop_price=data.get("stopPrice"),
            time_in_force=data.get("timeInForce"),
            update_time=data.get("updateTime"),
            raw=data,
        )

    @classmethod
    def from_error(cls, message: str) -> "OrderResult":
        """Build a failed OrderResult carrying an error message."""
        return cls(success=False, error_message=message)

    def summary_lines(self) -> list[str]:
        """Return a list of display lines for CLI output."""
        if not self.success:
            return [f"  ✗  Error: {self.error_message}"]

        lines = [
            f"  Order ID      : {self.order_id}",
            f"  Client OID    : {self.client_order_id}",
            f"  Symbol        : {self.symbol}",
            f"  Side          : {self.side}",
            f"  Type          : {self.order_type}",
            f"  Status        : {self.status}",
            f"  Orig Qty      : {self.orig_qty}",
            f"  Executed Qty  : {self.executed_qty}",
        ]

        avg = self.avg_price
        if avg and avg != "0" and avg != "0.00000":
            lines.append(f"  Avg Fill Price: {avg}")

        if self.price and self.price != "0":
            lines.append(f"  Limit Price   : {self.price}")

        if self.stop_price and self.stop_price != "0":
            lines.append(f"  Stop Price    : {self.stop_price}")

        if self.time_in_force:
            lines.append(f"  Time-in-Force : {self.time_in_force}")

        return lines


class OrderManager:
    """
    Manages order placement and lifecycle operations.

    Decouples the business logic from both the low-level HTTP client and the
    CLI presentation layer.
    """

    def __init__(self, client: BinanceFuturesClient) -> None:
        self._client = client

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None,
        time_in_force: Optional[str] = None,
        reduce_only: bool = False,
    ) -> OrderResult:
        """
        Place a futures order and return a normalised OrderResult.

        All parameters should already be validated by bot.validators before
        calling this method.

        Args:
            symbol:        Upper-cased trading pair.
            side:          'BUY' or 'SELL'.
            order_type:    'MARKET', 'LIMIT', or 'STOP_MARKET'.
            quantity:      Order quantity as Decimal.
            price:         Limit price as Decimal (LIMIT orders).
            stop_price:    Trigger price as Decimal (STOP_MARKET orders).
            time_in_force: 'GTC' / 'IOC' / 'FOK' (LIMIT only, defaults GTC).
            reduce_only:   Reduce-only flag.

        Returns:
            OrderResult (success or failure).
        """
        price_str = str(price) if price is not None else None
        stop_price_str = str(stop_price) if stop_price is not None else None
        qty_str = str(quantity)

        logger.info(
            "OrderManager placing | %s %s %s qty=%s price=%s stopPrice=%s",
            side,
            order_type,
            symbol,
            qty_str,
            price_str,
            stop_price_str,
        )

        try:
            response = self._client.new_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=qty_str,
                price=price_str,
                stop_price=stop_price_str,
                time_in_force=time_in_force,
                reduce_only=reduce_only,
            )
        except BinanceAPIError as exc:
            logger.error("API error placing order: %s", exc)
            return OrderResult.from_error(str(exc))
        except BinanceNetworkError as exc:
            logger.error("Network error placing order: %s", exc)
            return OrderResult.from_error(f"Network error: {exc}")
        except Exception as exc:
            logger.exception("Unexpected error placing order")
            return OrderResult.from_error(f"Unexpected error: {exc}")

        result = OrderResult.from_response(response)
        logger.info(
            "Order placed successfully | orderId=%s status=%s executedQty=%s",
            result.order_id,
            result.status,
            result.executed_qty,
        )
        return result

    def cancel_order(self, symbol: str, order_id: int) -> OrderResult:
        """
        Cancel an open order.

        Args:
            symbol:   Trading pair.
            order_id: Binance order ID.

        Returns:
            OrderResult reflecting the cancelled order.
        """
        logger.info("Cancelling orderId=%d on %s", order_id, symbol)
        try:
            response = self._client.cancel_order(symbol=symbol, order_id=order_id)
        except BinanceAPIError as exc:
            logger.error("API error cancelling order: %s", exc)
            return OrderResult.from_error(str(exc))
        except BinanceNetworkError as exc:
            logger.error("Network error cancelling order: %s", exc)
            return OrderResult.from_error(f"Network error: {exc}")
        except Exception as exc:
            logger.exception("Unexpected error cancelling order")
            return OrderResult.from_error(f"Unexpected error: {exc}")

        result = OrderResult.from_response(response)
        logger.info("Order cancelled | orderId=%s status=%s", result.order_id, result.status)
        return result
