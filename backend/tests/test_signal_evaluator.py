import json
import time
from decimal import Decimal

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent_store import AgentStore
from app.credential_crypto import encrypt_credential
from app.db import Base
from app.models import AgentSignalState, ExchangeCredential, LedgerEntry, LedgerTransaction, PaperOrder, User
from app.paper_store import PaperLedgerStore
from app.signal_evaluator import PaperSignalEvaluator


def test_approved_signal_routes_entry_and_exit_through_risk_and_balanced_ledger(monkeypatch):
    monkeypatch.setenv("MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    store = PaperLedgerStore(sessions)
    user_id = "00000000-0000-0000-0000-000000000001"
    with sessions() as session, session.begin():
        session.add(User(id=user_id, email="evaluator@example.com", password_hash="test", email_verified=True))

    connection_id = "00000000-0000-0000-0000-000000000002"
    ciphertext = encrypt_credential(json.dumps({
        "api_key": "test-key", "api_secret": "test-secret", "environment": "sandbox",
    }), user_id=user_id, credential_id=connection_id)
    with sessions() as session, session.begin():
        session.add(ExchangeCredential(id=connection_id, user_id=user_id, exchange="binance", label="test", ciphertext=ciphertext))

    strategy = {
        "symbol": "BTC/USDT", "allocated_capital": "100", "currency": "USDT",
        "max_risk_per_trade_percent": "1", "max_daily_loss_percent": "10",
        "stop_loss_percent": "2", "allowed_order_types": ["market"],
        "entry_conditions": [{"kind": "price_cross", "indicator": "PRICE", "operator": "crosses_above", "value": "100", "timeframe": "1m", "symbol": "BTCUSDT"}],
        "exit_conditions": [{"kind": "price_cross", "indicator": "PRICE", "operator": "crosses_below", "value": "95", "timeframe": "1m", "symbol": "BTCUSDT"}],
    }
    agents = AgentStore(sessions)
    agent = agents.create(user_id, "paper loop", strategy)
    agents.transition(agent["id"], user_id, "VALIDATING", False)
    agents.transition(agent["id"], user_id, "AWAITING_APPROVAL", False)
    agents.approve_strategy(agent["id"], user_id)

    class FakeAdapter:
        price = Decimal("101")

        def __init__(self, api_key, api_secret):
            assert (api_key, api_secret) == ("test-key", "test-secret")

        def get_klines(self, symbol, interval, limit=3):
            close_time = int(time.time() * 1000) - 60_000
            return [
                {"close": Decimal("98"), "volume": Decimal("10"), "close_time": close_time - 60_000},
                {"close": Decimal("99"), "volume": Decimal("12"), "close_time": close_time},
            ]

        def get_market_price(self, symbol):
            return {"symbol": symbol, "price": str(self.price)}

        def validate_quantity(self, symbol, quantity):
            step = Decimal("0.001")
            valid = quantity >= step and quantity % step == 0
            return {"valid": valid, "step_size": str(step), "reason": None if valid else "quantity_step_invalid"}

        def validate_price(self, symbol, price):
            tick = Decimal("0.01")
            return {"valid": price > 0 and price % tick == 0, "tick_size": str(tick), "reason": "price_tick_invalid"}

    import app.signal_evaluator as evaluator_module
    monkeypatch.setattr(evaluator_module, "BinanceTestnetAdapter", FakeAdapter)
    evaluator = PaperSignalEvaluator(store)
    first = evaluator.run_once()
    assert first == {"checked": 1, "orders": 1, "rejected": 0, "skipped": 0, "errors": 0}

    FakeAdapter.price = Decimal("90")
    second = evaluator.run_once()
    assert second == {"checked": 1, "orders": 1, "rejected": 0, "skipped": 0, "errors": 0}

    def temporary_network_failure(*args, **kwargs):
        raise ConnectionError("temporary network jitter")

    monkeypatch.setattr(evaluator, "evaluate_agent", temporary_network_failure)
    failed_cycle = evaluator.run_once()
    assert failed_cycle == {"checked": 1, "orders": 0, "rejected": 0, "skipped": 1, "errors": 1}
    with sessions() as session:
        signal_state = session.get(AgentSignalState, agent["id"])
        assert signal_state.last_status == "ERROR"
        assert signal_state.last_error_code == "ConnectionError"

    with sessions() as session:
        orders = session.scalars(select(PaperOrder).order_by(PaperOrder.created_at)).all()
        assert [order.side for order in orders] == ["BUY", "SELL"]
        assert all(order.status == "FILLED" for order in orders)
        entries = session.scalars(select(LedgerEntry)).all()
        transaction_ids = {entry.transaction_id for entry in entries}
        transactions = session.scalars(select(LedgerTransaction).where(LedgerTransaction.id.in_(transaction_ids))).all()
        for transaction in transactions:
            debit = session.scalar(select(func.sum(LedgerEntry.amount)).where(LedgerEntry.transaction_id == transaction.id, LedgerEntry.side == "DEBIT")) or Decimal("0")
            credit = session.scalar(select(func.sum(LedgerEntry.amount)).where(LedgerEntry.transaction_id == transaction.id, LedgerEntry.side == "CREDIT")) or Decimal("0")
            assert Decimal(debit) == Decimal(credit)
    assert store.verify_ledger(agent["paper_account_id"])["balanced"] is True
    engine.dispose()
