import logging

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.db import Base
from app.email_delivery import EmailService
from app.identity import IdentityStore, PasswordResetError
from app.rate_limit import _local_counts, _local_lock, limit_login
from app.exchanges.binance_testnet import BinanceTestnetAdapter, ExchangeError
from app.ai_strategy import (
    OpenAIResponsesProvider, StrategyCondition, StructuredStrategy, validate_interpretation,
)
from decimal import Decimal


def _request():
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                    "client": ("198.51.100.10", 1234), "server": ("test", 80), "scheme": "http"})


def test_login_rate_limit_rejects_after_threshold(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    with _local_lock:
        _local_counts.clear()
    for _ in range(5):
        limit_login(_request(), "limited@example.test")
    with pytest.raises(HTTPException) as error:
        limit_login(_request(), "limited@example.test")
    assert error.value.status_code == 429
    assert "Retry-After" in error.value.headers


def test_local_email_service_logs_one_time_token(caplog):
    with caplog.at_level(logging.WARNING, logger="tradegen.email"):
        EmailService(production=False).send_verification("dev@example.test", "dev-only-token")
    assert "dev-only-token" in caplog.text


def test_password_reset_is_single_use_and_revokes_existing_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    identity = IdentityStore(sessionmaker(bind=engine, expire_on_commit=False))
    user, verification = identity.register("reset@example.test", "Original password 123!")
    identity.verify_email(verification)
    _, session_token = identity.login("reset@example.test", "Original password 123!")
    reset_token = identity.request_password_reset("reset@example.test")
    assert reset_token
    identity.complete_password_reset(reset_token, "Updated password 456!")
    assert identity.current_user(session_token) is None
    assert identity.login("reset@example.test", "Updated password 456!")[0]["id"] == user["id"]
    with pytest.raises(PasswordResetError):
        identity.complete_password_reset(reset_token, "Another password 789!")
    engine.dispose()


def test_binance_price_validation_uses_exchange_tick_size(monkeypatch):
    from app.exchanges import binance_testnet

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit):
            return b'{"symbols":[{"filters":[{"filterType":"PRICE_FILTER","minPrice":"0.01","maxPrice":"1000000.00","tickSize":"0.01"},{"filterType":"LOT_SIZE","minQty":"0.001","maxQty":"1000.000","stepSize":"0.001"}]}]}'

    monkeypatch.setattr(binance_testnet, "urlopen", lambda request, timeout: Response())
    adapter = BinanceTestnetAdapter("key", "secret")
    assert adapter.validate_price("BTCUSDT", Decimal("10.25"))["valid"] is True
    invalid = adapter.validate_price("BTCUSDT", Decimal("10.251"))
    assert invalid["valid"] is False and invalid["reason"] == "price_not_aligned_to_tick"
    assert adapter.validate_quantity("BTCUSDT", Decimal("0.012"))["valid"] is True
    assert adapter.validate_quantity("BTCUSDT", Decimal("0.0125"))["reason"] == "quantity_not_aligned_to_step"
    with pytest.raises(ExchangeError) as error:
        adapter.get_market_price("BTC/USDT")
    assert error.value.code == "invalid_symbol"


def _condition(indicator, operator, value, *, kind="indicator_threshold", symbol="BTCUSDT"):
    return StrategyCondition(kind=kind, indicator=indicator, operator=operator, value=value,
                             timeframe="15m", symbol=symbol, period=14 if indicator == "RSI" else None)


def test_interpretation_accepts_only_explicit_complete_conditions():
    parsed = StructuredStrategy(
        status="ready", entry_conditions=[_condition("RSI", "gt", "70")],
        exit_conditions=[_condition("RSI", "lt", "30")], missing_information=[],
        ambiguities=[], unsupported_instructions=[], explanation="RSI rules parsed.",
    )
    result = validate_interpretation("BTCUSDT RSI 14 above 70 on 15m; exit RSI 14 below 30 on 15m", parsed)
    assert result.status == "ready"

    invented_threshold = StructuredStrategy(
        status="ready", entry_conditions=[_condition("PRICE", "gt", "2")],
        exit_conditions=[_condition("RSI", "lt", "30")], missing_information=[],
        ambiguities=[], unsupported_instructions=[], explanation="Parsed.",
    )
    vague = validate_interpretation("BTCUSDT buy when price rises slightly and exit RSI below 30 on 15m", invented_threshold)
    assert vague.status == "needs_clarification"
    assert any("threshold" in item for item in vague.missing_information)
    assert any("vague" in item.lower() for item in vague.ambiguities)


def test_price_conditions_require_exchange_tick_filter_validation():
    parsed = StructuredStrategy(
        status="ready", entry_conditions=[_condition("PRICE", "crosses_above", "65000", kind="price_cross")],
        exit_conditions=[_condition("RSI", "lt", "30")], missing_information=[],
        ambiguities=[], unsupported_instructions=[], explanation="Parsed.",
    )
    text = "BTCUSDT price crosses above 65000 on 15m, exit RSI 14 below 30 on 15m"
    pending = validate_interpretation(text, parsed)
    assert pending.status == "needs_clarification"
    rejected = validate_interpretation(text, parsed, lambda symbol, value: {"valid": False, "reason": "price_not_aligned_to_tick"})
    assert rejected.status == "needs_clarification"
    accepted = validate_interpretation(text, parsed, lambda symbol, value: {"valid": True})
    assert accepted.status == "ready"


def test_openai_provider_enables_strict_json_schema(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit):
            return b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"{\\"status\\":\\"needs_clarification\\",\\"entry_conditions\\":[],\\"exit_conditions\\":[],\\"missing_information\\":[\\"explicit rules\\"],\\"ambiguities\\":[],\\"unsupported_instructions\\":[],\\"explanation\\":\\"Please clarify.\\"}"}]}]}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["auth"] = request.get_header("Authorization")
        return Response()

    import json
    monkeypatch.setattr("app.ai_strategy.urlopen", fake_urlopen)
    provider = OpenAIResponsesProvider(api_key="unit-test-key", model="test-model")
    result = provider.interpret("parse this")
    assert result.status == "needs_clarification"
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["auth"] == "Bearer unit-test-key"
    assert captured["body"]["text"]["format"]["type"] == "json_schema"
    assert captured["body"]["text"]["format"]["strict"] is True
