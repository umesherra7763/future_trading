"""
Low-level Binance Futures Testnet client.

Responsibilities:
  - Build and sign every REST request (HMAC-SHA256).
  - Execute HTTP calls via a requests.Session with retries.
  - Log every outgoing request and incoming response at DEBUG level.
  - Translate HTTP / JSON errors into typed exceptions.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bot.logging_config import get_logger

logger = get_logger("bot.client")

# Binance Futures Testnet base URL
TESTNET_BASE_URL = "https://testnet.binancefuture.com"

# Timeout for every HTTP call (connect, read) in seconds
REQUEST_TIMEOUT = (5, 10)


class BinanceAPIError(Exception):
    """Raised when the Binance REST API returns an error payload."""

    def __init__(self, code: int, message: str, http_status: int = 0) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(f"Binance API error {code}: {message} (HTTP {http_status})")


class BinanceNetworkError(Exception):
    """Raised on connectivity or timeout failures."""


class BinanceFuturesClient:
    """
    Thin wrapper around the Binance USDT-M Futures Testnet REST API.

    Usage::

        client = BinanceFuturesClient(api_key="...", api_secret="...")
        response = client.new_order(symbol="BTCUSDT", side="BUY",
                                    order_type="MARKET", quantity="0.001")
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = TESTNET_BASE_URL,
        recv_window: int = 5000,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Both api_key and api_secret are required.")

        self._api_key = api_key
        self._api_secret = api_secret.encode()
        self._base_url = base_url.rstrip("/")
        self._recv_window = recv_window

        self._session = self._build_session()
        logger.debug(
            "BinanceFuturesClient initialised | base_url=%s recvWindow=%d",
            self._base_url,
            self._recv_window,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_session() -> requests.Session:
        """Create a requests.Session with automatic retries on transient errors."""
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist={500, 502, 503, 504},
            allowed_methods={"GET", "POST", "DELETE"},
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _sign(self, params: dict) -> dict:
        """Add timestamp, recvWindow, and HMAC-SHA256 signature to *params* in-place."""
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = self._recv_window
        query_string = urlencode(params)
        signature = hmac.new(
            self._api_secret, query_string.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    def _headers(self) -> dict[str, str]:
        return {"X-MBX-APIKEY": self._api_key}

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        signed: bool = False,
    ) -> Any:
        """
        Execute an HTTP request.

        Args:
            method:   HTTP verb ('GET', 'POST', 'DELETE').
            endpoint: Path relative to base URL (e.g. '/fapi/v1/order').
            params:   Query / body parameters.
            signed:   If True, add timestamp + signature.

        Returns:
            Parsed JSON response (dict or list).

        Raises:
            BinanceAPIError:     API returned a JSON error payload.
            BinanceNetworkError: Connectivity or timeout failure.
        """
        params = params or {}
        if signed:
            self._sign(params)

        url = f"{self._base_url}{endpoint}"
        headers = self._headers()

        # Log outgoing request (mask signature in logs)
        safe_params = {k: v for k, v in params.items() if k != "signature"}
        logger.debug(
            "→ %s %s | params=%s", method.upper(), endpoint, safe_params
        )

        try:
            if method.upper() == "GET":
                response = self._session.get(
                    url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
                )
            elif method.upper() == "POST":
                response = self._session.post(
                    url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
                )
            elif method.upper() == "DELETE":
                response = self._session.delete(
                    url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
                )
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

        except requests.exceptions.Timeout as exc:
            logger.error("Request timed out: %s %s", method, endpoint)
            raise BinanceNetworkError(f"Request timed out ({method} {endpoint})") from exc
        except requests.exceptions.ConnectionError as exc:
            logger.error("Connection error: %s %s | %s", method, endpoint, exc)
            raise BinanceNetworkError(
                f"Connection error — check network / testnet availability."
            ) from exc
        except requests.exceptions.RequestException as exc:
            logger.error("Unexpected request error: %s", exc)
            raise BinanceNetworkError(str(exc)) from exc

        # Log raw response status
        logger.debug(
            "← %d %s | %s %s",
            response.status_code,
            response.reason,
            method.upper(),
            endpoint,
        )

        # Parse JSON
        try:
            data = response.json()
        except ValueError:
            logger.error(
                "Non-JSON response (HTTP %d): %.500s",
                response.status_code,
                response.text,
            )
            raise BinanceAPIError(
                -1,
                f"Non-JSON response (HTTP {response.status_code})",
                response.status_code,
            )

        # Binance error payloads have a 'code' key that is negative
        if isinstance(data, dict) and data.get("code", 0) < 0:
            code = data["code"]
            message = data.get("msg", "unknown error")
            logger.error(
                "Binance API error | code=%d msg=%s http=%d",
                code,
                message,
                response.status_code,
            )
            raise BinanceAPIError(code, message, response.status_code)

        logger.debug("Response body: %s", data)
        return data

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def ping(self) -> dict:
        """Test connectivity to the REST API."""
        return self._request("GET", "/fapi/v1/ping")

    def get_server_time(self) -> dict:
        """Return server time (useful for clock drift diagnosis)."""
        return self._request("GET", "/fapi/v1/time")

    def get_exchange_info(self) -> dict:
        """Return exchange trading rules and symbol information."""
        return self._request("GET", "/fapi/v1/exchangeInfo")

    def get_account(self) -> dict:
        """Return current account information (requires auth)."""
        return self._request("GET", "/fapi/v2/account", signed=True)

    def get_open_orders(self, symbol: str | None = None) -> list:
        """Return all open orders, optionally filtered by symbol."""
        params = {}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request("GET", "/fapi/v1/openOrders", params=params, signed=True)

    def new_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: str,
        price: str | None = None,
        stop_price: str | None = None,
        time_in_force: str | None = None,
        reduce_only: bool = False,
    ) -> dict:
        """
        Place a new futures order.

        Args:
            symbol:        Trading pair (e.g. 'BTCUSDT').
            side:          'BUY' or 'SELL'.
            order_type:    'MARKET', 'LIMIT', or 'STOP_MARKET'.
            quantity:      Order quantity as a string.
            price:         Limit price (required for LIMIT orders).
            stop_price:    Trigger price (required for STOP_MARKET orders).
            time_in_force: 'GTC', 'IOC', 'FOK' (defaults to 'GTC' for LIMIT).
            reduce_only:   If True, order only reduces an existing position.

        Returns:
            Order response dict from Binance.
        """
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity,
        }

        if order_type.upper() == "LIMIT":
            if price is None:
                raise ValueError("Price is required for LIMIT orders.")
            params["price"] = price
            params["timeInForce"] = time_in_force or "GTC"

        if order_type.upper() == "STOP_MARKET":
            if stop_price is None:
                raise ValueError("Stop price is required for STOP_MARKET orders.")
            params["stopPrice"] = stop_price

        if reduce_only:
            params["reduceOnly"] = "true"

        logger.info(
            "Placing order | symbol=%s side=%s type=%s qty=%s price=%s stopPrice=%s",
            symbol,
            side,
            order_type,
            quantity,
            price,
            stop_price,
        )
        return self._request("POST", "/fapi/v1/order", params=params, signed=True)

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        """Cancel an open order by orderId."""
        params = {"symbol": symbol.upper(), "orderId": order_id}
        logger.info("Cancelling order | symbol=%s orderId=%d", symbol, order_id)
        return self._request("DELETE", "/fapi/v1/order", params=params, signed=True)
