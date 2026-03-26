"""
Input validation for order parameters.
All validators raise ValueError with a human-readable message on failure.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

VALID_SIDES = {"BUY", "SELL"}
VALID_ORDER_TYPES = {"MARKET", "LIMIT", "STOP_MARKET"}

# Reasonable sanity bounds — not Binance LOT_SIZE rules (those come from exchange filters)
MIN_QUANTITY = Decimal("0.00000001")
MAX_QUANTITY = Decimal("1_000_000")

# USDT-M Futures Testnet only supports USDT-margined pairs.
# Pairs ending in USDC, BUSD, etc. will return -1121 Invalid Symbol from the API.
USDT_M_QUOTE = "USDT"

# Common USDT-M testnet symbols for the helpful hint message
EXAMPLE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT"]


def validate_symbol(symbol: str) -> str:
    """
    Normalise and validate a trading pair symbol for USDT-M Futures Testnet.

    Rules:
    - Non-empty string
    - Uppercase letters and digits only
    - At least 5 characters (shortest valid USDT-M pair is XYZUSDT → 7, but allow some slack)
    - Must end with 'USDT' — USDT-M Testnet only supports USDT-margined pairs.
      Pairs like BTCUSDC or ETHBUSD will return an "Invalid Symbol" API error.

    Returns the upper-cased symbol.
    """
    if not symbol or not symbol.strip():
        raise ValueError("Symbol must not be empty.")

    symbol = symbol.strip().upper()

    if len(symbol) < 3:
        raise ValueError(f"Symbol '{symbol}' is too short (minimum 3 characters).")

    if not symbol.isalnum():
        raise ValueError(
            f"Symbol '{symbol}' contains invalid characters. "
            "Only letters and digits are allowed (e.g. BTCUSDT)."
        )

    # USDT-M Testnet specific check — warn clearly rather than let the API return -1121
    if not symbol.endswith(USDT_M_QUOTE):
        raise ValueError(
            f"Symbol '{symbol}' does not end with '{USDT_M_QUOTE}'.\n"
            f"  The USDT-M Futures Testnet only supports USDT-margined pairs.\n"
            f"  Pairs like BTCUSDC or ETHBUSD will return an 'Invalid Symbol' error.\n"
            f"  Try one of: {', '.join(EXAMPLE_SYMBOLS)}."
        )

    return symbol


def validate_side(side: str) -> str:
    """
    Validate order side.

    Returns the upper-cased side string ('BUY' or 'SELL').
    """
    if not side or not side.strip():
        raise ValueError("Side must not be empty.")

    side = side.strip().upper()

    if side not in VALID_SIDES:
        raise ValueError(
            f"Invalid side '{side}'. Must be one of: {', '.join(sorted(VALID_SIDES))}."
        )

    return side


def validate_order_type(order_type: str) -> str:
    """
    Validate order type.

    Returns the upper-cased order type string.
    """
    if not order_type or not order_type.strip():
        raise ValueError("Order type must not be empty.")

    order_type = order_type.strip().upper()

    if order_type not in VALID_ORDER_TYPES:
        raise ValueError(
            f"Invalid order type '{order_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_ORDER_TYPES))}."
        )

    return order_type


def validate_quantity(quantity: str | float) -> Decimal:
    """
    Validate and convert quantity to Decimal.

    Rules:
    - Must be a valid positive number
    - Must be within [MIN_QUANTITY, MAX_QUANTITY]

    Returns a Decimal.
    """
    try:
        qty = Decimal(str(quantity))
    except InvalidOperation:
        raise ValueError(f"Quantity '{quantity}' is not a valid number.")

    if qty <= 0:
        raise ValueError(f"Quantity must be positive, got {qty}.")

    if qty < MIN_QUANTITY:
        raise ValueError(f"Quantity {qty} is below the minimum allowed ({MIN_QUANTITY}).")

    if qty > MAX_QUANTITY:
        raise ValueError(f"Quantity {qty} exceeds the maximum allowed ({MAX_QUANTITY}).")

    return qty


def validate_price(price: str | float | None, order_type: str) -> Optional[Decimal]:
    """
    Validate and convert price to Decimal.

    Rules:
    - Required when order_type is LIMIT or STOP_MARKET
    - Must be a valid positive number when provided

    Returns a Decimal or None for MARKET orders.
    """
    order_type = order_type.upper()

    if order_type == "MARKET":
        return None

    # LIMIT and STOP_MARKET require a price
    if price is None:
        raise ValueError(f"Price is required for {order_type} orders.")

    try:
        p = Decimal(str(price))
    except InvalidOperation:
        raise ValueError(f"Price '{price}' is not a valid number.")

    if p <= 0:
        raise ValueError(f"Price must be positive, got {p}.")

    return p


def validate_stop_price(
    stop_price: str | float | None, order_type: str
) -> Optional[Decimal]:
    """
    Validate stop price for STOP_MARKET orders.

    Returns a Decimal or None.
    """
    order_type = order_type.upper()

    if order_type != "STOP_MARKET":
        return None

    if stop_price is None:
        raise ValueError("Stop price (--stop-price) is required for STOP_MARKET orders.")

    try:
        sp = Decimal(str(stop_price))
    except InvalidOperation:
        raise ValueError(f"Stop price '{stop_price}' is not a valid number.")

    if sp <= 0:
        raise ValueError(f"Stop price must be positive, got {sp}.")

    return sp


def validate_all(
    symbol: str,
    side: str,
    order_type: str,
    quantity: str | float,
    price: str | float | None = None,
    stop_price: str | float | None = None,
) -> dict:
    """
    Run all validators and return a clean, typed parameter dict.

    Raises ValueError if any field is invalid.
    """
    validated_type = validate_order_type(order_type)
    return {
        "symbol":     validate_symbol(symbol),
        "side":       validate_side(side),
        "order_type": validated_type,
        "quantity":   validate_quantity(quantity),
        "price":      validate_price(price, validated_type),
        "stop_price": validate_stop_price(stop_price, validated_type),
    }
