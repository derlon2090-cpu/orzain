import asyncio
from decimal import Decimal
from sqlalchemy import select

from app.execution import BaseExecutionAdapter, PaperTradingEngine
from app.orchestrator import AISignal, TradeOrchestrator
from app.risk_engine import RiskEngine
from app.trading_models import OrderSide, OrderType, ProposedOrder, RiskErrorCode, RiskProfile


def order(**overrides):
    values = dict(agent_id="agent-1", symbol="BTC/USDT", side=OrderSide.BUY,
                  order_type=OrderType.LIMIT, quantity=Decimal("0.1"), price=Decimal("100"),
                  stop_loss_price=Decimal("95"))
    values.update(overrides)
    return ProposedOrder(**values)


def profile(**overrides):
    values = dict(max_capital_per_order=Decimal("100"), max_daily_loss=Decimal("3"),
                  allowed_symbols=["BTC/USDT"], allow_leverage=False, emergency_stop=False)
    values.update(overrides)
    return RiskProfile(**values)


def test_risk_pipeline_allows_valid_order_using_decimal():
    result = RiskEngine().evaluate_order(order(), profile(), Decimal("0"), Decimal("100"))
    assert result.passed is True
    assert result.rejection_reason is None


def test_emergency_stop_has_first_rejection_code():
    result = RiskEngine().evaluate_order(order(), profile(emergency_stop=True), Decimal("0"), Decimal("100"))
    assert result.rejection_reason == RiskErrorCode.EMERGENCY_STOP_ACTIVE


def test_symbol_notional_daily_loss_and_balance_limits_are_enforced():
    engine = RiskEngine()
    assert engine.evaluate_order(order(symbol="ETH/USDT"), profile(), Decimal("0"), Decimal("100")).rejection_reason == RiskErrorCode.SYMBOL_NOT_PERMITTED
    assert engine.evaluate_order(order(quantity=Decimal("2")), profile(), Decimal("0"), Decimal("1000")).rejection_reason == RiskErrorCode.MAX_ORDER_CAPITAL_EXCEEDED
    assert engine.evaluate_order(order(), profile(), Decimal("3"), Decimal("100")).rejection_reason == RiskErrorCode.DAILY_DRAWDOWN_LIMIT_REACHED
    assert engine.evaluate_order(order(), profile(), Decimal("0"), Decimal("9")).rejection_reason == RiskErrorCode.INSUFFICIENT_UNLEVERAGED_BALANCE


def test_risk_check_rejects_unapproved_state_and_stale_data():
    engine = RiskEngine()
    assert engine.evaluate_order(order(), profile(), Decimal("0"), Decimal("100"), authorized=False).rejection_reason == RiskErrorCode.AGENT_NOT_AUTHORIZED
    assert engine.evaluate_order(order(), profile(), Decimal("0"), Decimal("100"), data_age_seconds=16).rejection_reason == RiskErrorCode.STALE_MARKET_DATA


def test_stop_loss_direction_and_risk_are_calculated_server_side():
    engine = RiskEngine()
    assert engine.evaluate_order(order(), profile(), Decimal("0"), Decimal("100"), max_trade_risk=Decimal("0.49")).rejection_reason == RiskErrorCode.TRADE_RISK_LIMIT_EXCEEDED
    assert engine.evaluate_order(order(stop_loss_price=Decimal("101")), profile(), Decimal("0"), Decimal("100"), max_trade_risk=Decimal("1")).rejection_reason == RiskErrorCode.STOP_LOSS_INVALID


def test_paper_engine_applies_fees_slippage_and_replays_idempotent_result():
    engine = PaperTradingEngine(Decimal("100"), fee_rate=Decimal("0.01"), slippage_rate=Decimal("0.01"))
    proposed = order()
    first = asyncio.run(engine.place_order(proposed, "signal-1"))
    retry = asyncio.run(engine.place_order(proposed, "signal-1"))
    assert first["status"] == "FILLED"
    assert first["executed_price"] == "101.00"
    assert first["fee"] == "0.10100"
    assert retry == first
    assert len(engine.executed_orders) == 1
    assert engine.balance == Decimal("89.79900")


def test_paper_engine_rejects_sell_without_inventory():
    engine = PaperTradingEngine(Decimal("100"))
    result = asyncio.run(engine.place_order(order(side=OrderSide.SELL), "sell-1"))
    assert result["reason"] == "INSUFFICIENT_PAPER_POSITION"


def test_paper_engine_tracks_realized_daily_loss_from_fills():
    engine = PaperTradingEngine(Decimal("100"), fee_rate=Decimal("0"), slippage_rate=Decimal("0"))
    asyncio.run(engine.place_order(order(quantity=Decimal("0.5")), "buy-1"))
    sold = asyncio.run(engine.place_order(order(side=OrderSide.SELL, quantity=Decimal("0.5"), price=Decimal("90")), "sell-1"))
    assert sold["realized_pnl"] == "-5.0"
    assert engine.current_daily_loss() == Decimal("5.0")


class NeverExecute(BaseExecutionAdapter):
    def __init__(self): self.called = False
    async def place_order(self, order, idempotency_key):
        self.called = True
        return {"status": "FILLED"}
    async def cancel_order(self, order_id): return False


def test_orchestrator_audits_rejection_without_calling_adapter():
    adapter = NeverExecute()
    audit = []
    orchestrator = TradeOrchestrator(RiskEngine(), adapter, audit)
    signal = AISignal(agent_id="agent-1", symbol="ETH/USDT", action=OrderSide.BUY,
                      amount=Decimal("0.1"), price=Decimal("100"), stop_loss_price=Decimal("95"), signal_id="s-1")
    result = asyncio.run(orchestrator.handle_ai_signal(
        signal, profile(), Decimal("0"), Decimal("100"), authorized=True,
        permitted_order_types={"LIMIT"}, max_trade_risk=Decimal("1"),
    ))
    assert result["success"] is False
    assert result["error"] == RiskErrorCode.SYMBOL_NOT_PERMITTED.value
    assert adapter.called is False
    assert audit == [{"agent_id": "agent-1", "symbol": "ETH/USDT", "stage": "RISK_ENGINE_REJECTION", "error_code": "SYMBOL_NOT_PERMITTED"}]


def test_api_paper_order_requires_approval_and_uses_idempotent_pipeline(monkeypatch):
    from fastapi.testclient import TestClient
    from app.db import Base
    from app.main import app, get_paper_store
    from app.paper_store import PaperLedgerStore
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(test_engine)
    test_store = PaperLedgerStore(sessionmaker(bind=test_engine, expire_on_commit=False, autoflush=False))
    app.dependency_overrides[get_paper_store] = lambda: test_store
    client = TestClient(app)
    monkeypatch.setenv("MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    assert client.get("/api/v1/agents").status_code == 401
    assert client.post("/api/v1/ai/interpret-strategy", json={"instruction": "test"}).status_code == 401
    registration = client.post("/api/v1/auth/register", json={"email": "owner@example.com", "password": "Correct horse battery 42!"})
    assert registration.status_code == 201
    assert registration.json()["user"]["email_verified"] is False
    verification_token = registration.json()["verification_token"]
    assert client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "Correct horse battery 42!"}).status_code == 403
    assert client.post("/api/v1/auth/verify-email", json={"token": verification_token}).status_code == 200
    assert client.post("/api/v1/auth/verify-email", json={"token": verification_token}).status_code == 400
    signed_in = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "Correct horse battery 42!"})
    assert signed_in.status_code == 200
    cookie = signed_in.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/v1/auth/me").status_code == 200
    from app.ai_strategy import StructuredStrategy, StrategyCondition
    class FakeStrategyProvider:
        def interpret(self, instruction):
            return StructuredStrategy(
                status="ready",
                entry_conditions=[StrategyCondition(kind="indicator_threshold", indicator="RSI", operator="gt", value="70", timeframe="15m", symbol="BTCUSDT", period=14)],
                exit_conditions=[StrategyCondition(kind="indicator_threshold", indicator="RSI", operator="lt", value="30", timeframe="15m", symbol="BTCUSDT", period=14)],
                missing_information=[], ambiguities=[], unsupported_instructions=[], explanation="Parsed explicit conditions.",
            )
    import app.main as main_module
    monkeypatch.setattr(main_module, "get_ai_provider", FakeStrategyProvider)
    interpreted = client.post("/api/v1/ai/interpret-strategy", json={
        "instruction": "BTCUSDT RSI 14 above 70 on 15m; exit RSI 14 below 30 on 15m",
    })
    assert interpreted.status_code == 200 and interpreted.json()["status"] == "ready"
    secret_key, secret_value = "paper-api-key-do-not-return", "paper-api-secret-do-not-return"
    connected = client.post("/api/v1/exchange-connections", json={
        "exchange": "binance", "label": "sandbox", "api_key": secret_key,
        "api_secret": secret_value, "environment": "sandbox",
    })
    assert connected.status_code == 201
    assert secret_key not in connected.text and secret_value not in connected.text
    assert client.get("/api/v1/exchange-connections").json()[0]["masked"] == "••••••"
    from app.models import ExchangeCredential
    with test_store.sessions() as session:
        stored_credential = session.scalar(select(ExchangeCredential))
        assert secret_key not in stored_credential.ciphertext
        assert secret_value not in stored_credential.ciphertext

    from app.exchanges import binance_testnet
    class FakeExchangeResponse:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit): return self.payload
    def fake_urlopen(request, timeout):
        assert "testnet.binance.vision" in request.full_url
        if request.full_url.endswith("/api/v3/account?recvWindow=5000&timestamp="):
            raise AssertionError("expected signed account query")
        if "/api/v3/account?" in request.full_url:
            assert request.get_header("X-mbx-apikey") == secret_key
            return FakeExchangeResponse(b'{"balances":[{"asset":"USDT","free":"1000.00","locked":"0.00"}]}')
        if "/api/v3/ticker/price?symbol=BTCUSDT" in request.full_url:
            return FakeExchangeResponse(b'{"symbol":"BTCUSDT","price":"65000.00"}')
        raise AssertionError(request.full_url)
    monkeypatch.setattr(binance_testnet, "urlopen", fake_urlopen)
    test_result = client.post(f"/api/v1/exchange-connections/{connected.json()['id']}/test")
    assert test_result.status_code == 200
    assert test_result.json()["balances"] == [{"asset": "USDT", "free": "1000.00", "locked": "0.00"}]
    assert secret_key not in test_result.text and secret_value not in test_result.text
    market_price = client.get(f"/api/v1/exchange-connections/{connected.json()['id']}/price?symbol=BTCUSDT")
    assert market_price.status_code == 200 and market_price.json()["price"] == "65000.00"
    raw_session = client.cookies.get("tradegen_session")
    from hashlib import sha256
    from app.models import UserSession
    with test_store.sessions() as session:
        stored_session = session.scalar(select(UserSession))
        assert stored_session.token_hash == sha256(raw_session.encode()).hexdigest()
    created = client.post("/api/v1/agents", json={
        "name": "API test agent",
        "strategy": {
            "symbol": "BTC/USDT", "allocated_capital": "100", "max_risk_per_trade_percent": "1",
            "max_daily_loss_percent": "3", "stop_loss_percent": "2",
            "entry_conditions": [{"kind":"price_cross","indicator":"PRICE","operator":"crosses_above","value":"100","timeframe":"1m","symbol":"BTCUSDT"}],
            "exit_conditions": [{"kind":"price_cross","indicator":"PRICE","operator":"crosses_below","value":"95","timeframe":"1m","symbol":"BTCUSDT"}], "allowed_order_types": ["limit"],
        },
    })
    assert created.status_code == 201
    agent_id = created.json()["id"]
    assert client.post(f"/api/v1/agents/{agent_id}/transition", json={"target": "PAPER_TRADING", "explicit_approval": True}).status_code == 403
    assert client.post(f"/api/v1/agents/{agent_id}/transition", json={"target": "VALIDATING"}).status_code == 200
    assert client.post(f"/api/v1/agents/{agent_id}/transition", json={"target": "AWAITING_APPROVAL"}).status_code == 200
    approved = client.post(f"/api/v1/agents/{agent_id}/approve", json={"explicit_approval": True})
    assert approved.status_code == 200 and approved.json()["strategy_version"] == 2
    assert approved.json()["approval_status"] == "APPROVED" and approved.json()["state"] == "PAPER_TRADING"

    payload = {"symbol": "BTC/USDT", "side": "buy", "order_type": "limit", "quantity": "0.1", "reference_price": "100", "stop_loss_price": "95"}
    first = client.post(f"/api/v1/agents/{agent_id}/paper-orders", json=payload, headers={"Idempotency-Key": "api-signal-1"})
    retry = client.post(f"/api/v1/agents/{agent_id}/paper-orders", json=payload, headers={"Idempotency-Key": "api-signal-1"})
    assert first.status_code == retry.status_code == 201
    assert first.json()["status"] == "FILLED"
    assert first.json()["execution"] == retry.json()["execution"]
    matching = [item for item in client.get("/api/v1/orders").json() if item["agent_id"] == agent_id]
    assert len(matching) == 1

    other_client = TestClient(app)
    other_registration = other_client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "Correct horse battery 43!"})
    assert other_registration.status_code == 201
    assert other_client.post("/api/v1/auth/verify-email", json={"token": other_registration.json()["verification_token"]}).status_code == 200
    assert other_client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "Correct horse battery 43!"}).status_code == 200
    assert other_client.get(f"/api/v1/agents/{agent_id}").status_code == 404
    assert other_client.post(f"/api/v1/agents/{agent_id}/transition", json={"target": "STOPPED"}).status_code == 404
    assert other_client.post(f"/api/v1/agents/{agent_id}/paper-orders", json=payload, headers={"Idempotency-Key": "foreign-order"}).status_code == 404
    assert other_client.get("/api/v1/orders").json() == []
    assert other_client.get("/api/v1/exchange-connections").json() == []
    assert other_client.post(f"/api/v1/exchange-connections/{connected.json()['id']}/test").status_code == 404
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    app.dependency_overrides.clear()
    test_engine.dispose()

