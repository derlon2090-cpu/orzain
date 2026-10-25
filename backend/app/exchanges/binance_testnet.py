"""Binance Spot Testnet adapter. This module has no order-placement operation."""
from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://testnet.binance.vision"
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{5,20}$")


class ExchangeError(Exception):
    """Safe-to-report adapter error with a stable category."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class BaseExchangeAdapter(ABC):
    @abstractmethod
    def test_connection(self) -> dict[str, Any]: ...

    @abstractmethod
    def get_balances(self) -> list[dict[str, str]]: ...

    @abstractmethod
    def get_market_price(self, symbol: str) -> dict[str, str]: ...

    @abstractmethod
    def get_klines(self, symbol: str, interval: str, limit: int = 3) -> list[dict[str, Any]]: ...

    @abstractmethod
    def validate_price(self, symbol: str, price: Decimal) -> dict[str, Any]: ...

    @abstractmethod
    def validate_quantity(self, symbol: str, quantity: Decimal) -> dict[str, Any]: ...


class BinanceTestnetAdapter(BaseExchangeAdapter):
    def __init__(self, api_key: str, api_secret: str, *, timeout_seconds: float = 3.0):
        if not api_key or not api_secret:
            raise ExchangeError("invalid_credentials")
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")
        self._timeout = min(max(timeout_seconds, 0.5), 5.0)

    def _request(self, path: str, params: dict[str, Any] | None = None, *, signed: bool = False) -> Any:
        parameters = dict(params or {})
        headers = {"Accept": "application/json", "User-Agent": "TRADEGEN-Sandbox/1.0"}
        if signed:
            parameters["recvWindow"] = 5000
            parameters["timestamp"] = int(time.time() * 1000)
            payload = urlencode(parameters)
            parameters["signature"] = hmac.new(self._api_secret, payload.encode("ascii"), hashlib.sha256).hexdigest()
            headers["X-MBX-APIKEY"] = self._api_key
        url = f"{BASE_URL}{path}"
        if parameters:
            url += "?" + urlencode(parameters)
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=self._timeout) as response:
                body = response.read(1_000_001)
                if len(body) > 1_000_000:
                    raise ExchangeError("invalid_response")
                return json.loads(body)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise ExchangeError("authentication_failed") from None
            if exc.code == 429 or exc.code == 418:
                raise ExchangeError("exchange_rate_limited") from None
            if exc.code >= 500:
                raise ExchangeError("exchange_unavailable") from None
            try:
                error_body = json.loads(exc.read(4096))
                if error_body.get("code") in (-2014, -2015, -1022):
                    raise ExchangeError("authentication_failed") from None
                if error_body.get("code") == -1121:
                    raise ExchangeError("invalid_symbol") from None
            except (ValueError, AttributeError):
                pass
            raise ExchangeError("exchange_rejected_request") from None
        except (URLError, TimeoutError, OSError):
            raise ExchangeError("exchange_unavailable") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ExchangeError("invalid_response") from None

    def get_balances(self) -> list[dict[str, str]]:
        response = self._request("/api/v3/account", signed=True)
        if not isinstance(response, dict) or not isinstance(response.get("balances"), list):
            raise ExchangeError("invalid_response")
        balances = []
        for item in response["balances"]:
            if not isinstance(item, dict) or not isinstance(item.get("asset"), str):
                raise ExchangeError("invalid_response")
            try:
                free, locked = Decimal(item["free"]), Decimal(item["locked"])
            except (KeyError, InvalidOperation, TypeError):
                raise ExchangeError("invalid_response") from None
            if not free.is_finite() or not locked.is_finite() or free < 0 or locked < 0:
                raise ExchangeError("invalid_response")
            if free or locked:
                balances.append({"asset": item["asset"], "free": str(free), "locked": str(locked)})
        return balances

    def test_connection(self) -> dict[str, Any]:
        # Authenticated account data proves both reachability and credential validity.
        balances = self.get_balances()
        return {"status": "connected", "balances": balances}

    def get_market_price(self, symbol: str) -> dict[str, str]:
        symbol = self._symbol(symbol)
        response = self._request("/api/v3/ticker/price", {"symbol": symbol})
        try:
            price = Decimal(response["price"])
        except (TypeError, KeyError, InvalidOperation):
            raise ExchangeError("invalid_response") from None
        if not price.is_finite() or price <= 0:
            raise ExchangeError("invalid_response")
        return {"symbol": symbol, "price": str(price)}

    def get_klines(self, symbol: str, interval: str, limit: int = 3) -> list[dict[str, Any]]:
        symbol = self._symbol(symbol)
        if interval not in {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"} or not 2 <= limit <= 200:
            raise ExchangeError("invalid_market_query")
        response = self._request("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        if not isinstance(response, list):
            raise ExchangeError("invalid_response")
        result = []
        try:
            for row in response:
                candle = {"open_time": int(row[0]), "open": Decimal(row[1]), "high": Decimal(row[2]),
                          "low": Decimal(row[3]), "close": Decimal(row[4]), "volume": Decimal(row[5]),
                          "close_time": int(row[6])}
                if any(not candle[key].is_finite() for key in ("open", "high", "low", "close", "volume")) or candle["volume"] < 0:
                    raise ValueError("invalid candle")
                result.append(candle)
        except (IndexError, TypeError, ValueError, InvalidOperation):
            raise ExchangeError("invalid_response") from None
        return result

    def validate_price(self, symbol: str, price: Decimal) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        if not price.is_finite() or price <= 0:
            return {"valid": False, "reason": "price_must_be_positive"}
        response = self._request("/api/v3/exchangeInfo", {"symbol": symbol})
        try:
            symbols = response["symbols"]
            rules = symbols[0]["filters"]
            price_filter = next(item for item in rules if item.get("filterType") == "PRICE_FILTER")
            minimum, maximum, tick = (Decimal(price_filter[k]) for k in ("minPrice", "maxPrice", "tickSize"))
        except (TypeError, KeyError, IndexError, StopIteration, InvalidOperation):
            raise ExchangeError("invalid_response") from None
        if tick <= 0:
            raise ExchangeError("invalid_response")
        if price < minimum or price > maximum:
            return {"valid": False, "reason": "price_outside_exchange_bounds", "min_price": str(minimum), "max_price": str(maximum), "tick_size": str(tick)}
        if price % tick != 0:
            return {"valid": False, "reason": "price_not_aligned_to_tick", "min_price": str(minimum), "max_price": str(maximum), "tick_size": str(tick)}
        return {"valid": True, "min_price": str(minimum), "max_price": str(maximum), "tick_size": str(tick)}

    def validate_quantity(self, symbol: str, quantity: Decimal) -> dict[str, Any]:
        symbol = self._symbol(symbol)
        if not quantity.is_finite() or quantity <= 0:
            return {"valid": False, "reason": "quantity_must_be_positive"}
        response = self._request("/api/v3/exchangeInfo", {"symbol": symbol})
        try:
            filters = response["symbols"][0]["filters"]
            lot_filter = next(item for item in filters if item.get("filterType") == "LOT_SIZE")
            minimum, maximum, step = (Decimal(lot_filter[k]) for k in ("minQty", "maxQty", "stepSize"))
        except (TypeError, KeyError, IndexError, StopIteration, InvalidOperation):
            raise ExchangeError("invalid_response") from None
        if step <= 0:
            raise ExchangeError("invalid_response")
        if quantity < minimum or quantity > maximum:
            return {"valid": False, "reason": "quantity_outside_exchange_bounds", "min_quantity": str(minimum), "max_quantity": str(maximum), "step_size": str(step)}
        if quantity % step != 0:
            return {"valid": False, "reason": "quantity_not_aligned_to_step", "min_quantity": str(minimum), "max_quantity": str(maximum), "step_size": str(step)}
        return {"valid": True, "min_quantity": str(minimum), "max_quantity": str(maximum), "step_size": str(step)}

    @staticmethod
    def _symbol(symbol: str) -> str:
        normalized = symbol.strip().upper()
        if not SYMBOL_PATTERN.fullmatch(normalized):
            raise ExchangeError("invalid_symbol")
        return normalized
