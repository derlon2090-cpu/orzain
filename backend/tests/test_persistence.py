from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.agent_store import AgentStore, InvalidAgentTransition
from app.models import User
from app.paper_store import IdempotencyConflict, PaperLedgerStore
from app.trading_models import OrderSide, OrderType, ProposedOrder
from app.credential_crypto import CredentialEncryptionError, decrypt_credential, encrypt_credential


@pytest.fixture
def store():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with sessions() as session, session.begin():
        session.add(User(id="test-user", email="test@example.com", password_hash="!", is_active=True, email_verified=True))
    yield PaperLedgerStore(sessions)
    engine.dispose()


def proposal(**overrides):
    fields = dict(agent_id="agent-p", symbol="BTC/USDT", side=OrderSide.BUY,
                  order_type=OrderType.LIMIT, quantity=Decimal("0.1"),
                  price=Decimal("100"), stop_loss_price=Decimal("95"))
    fields.update(overrides)
    return ProposedOrder(**fields)


def test_account_funding_and_order_ledger_are_balanced(store):
    account = store.create_account("test-user", Decimal("100"))
    result = store.place_order(account["id"], proposal(), "buy-1")
    assert result["status"] == "FILLED"
    assert store.verify_ledger(account["id"])["balanced"] is True
    state = store.get_account(account["id"])
    assert state["status"] == "ACTIVE"
    assert Decimal(state["reserved_balance"]) == 0
    assert Decimal(state["available_balance"]) < Decimal("100")


def test_crash_after_durable_order_keeps_account_fenced_until_reconciliation(store):
    account = store.create_account("test-user", Decimal("100"))
    pending = store.place_order(account["id"], proposal(), "crash-key", fail_after_record=True)
    assert pending["status"] == "PENDING_RECONCILIATION"

    # A separate store instance models a restarted process reading the same durable database.
    restarted = PaperLedgerStore(store.sessions)
    state = restarted.get_account(account["id"])
    assert state["status"] == "RECONCILIATION_REQUIRED"
    reserved_before = Decimal(state["reserved_balance"])
    available_before = Decimal(state["available_balance"])

    retry = restarted.place_order(account["id"], proposal(), "crash-key")
    assert retry["status"] == "PENDING_RECONCILIATION"
    assert Decimal(restarted.get_account(account["id"])["reserved_balance"]) == reserved_before

    settled = restarted.reconcile_order(pending["order_id"])
    assert settled["status"] == "FILLED"
    assert restarted.get_account(account["id"])["status"] == "ACTIVE"
    repeated_settlement = restarted.reconcile_order(pending["order_id"])
    assert repeated_settlement == settled
    assert restarted.get_account(account["id"])["available_balance"] == str(available_before)
    assert restarted.verify_ledger(account["id"])["balanced"] is True


def test_idempotency_key_cannot_be_reused_for_different_payload(store):
    account = store.create_account("test-user", Decimal("100"))
    store.place_order(account["id"], proposal(), "same-key")
    with pytest.raises(IdempotencyConflict):
        store.place_order(account["id"], proposal(quantity=Decimal("0.2")), "same-key")


def test_insufficient_funds_is_a_durable_rejection_without_ledger_mutation(store):
    account = store.create_account("test-user", Decimal("1"))
    before = store.verify_ledger(account["id"])["transaction_count"]
    rejected = store.place_order(account["id"], proposal(), "too-large")
    assert rejected["status"] == "REJECTED"
    assert rejected["rejection_code"] == "INSUFFICIENT_PAPER_FUNDS"
    assert store.verify_ledger(account["id"])["transaction_count"] == before


def test_risk_rejection_can_be_recorded_durably_and_replayed(store):
    account = store.create_account("test-user", Decimal("100"))
    denied_order = proposal(symbol="ETH/USDT")
    rejection = store.record_rejection(account["id"], denied_order, "risk-deny", "SYMBOL_NOT_PERMITTED")
    replay = store.get_order_by_idempotency(account["id"], "risk-deny", denied_order)
    assert rejection["status"] == replay["status"] == "REJECTED"
    assert replay["rejection_code"] == "SYMBOL_NOT_PERMITTED"
    assert store.get_account(account["id"])["available_balance"] == "100.00000000"


def test_agent_strategy_and_state_survive_store_restart_and_are_audited(store):
    agents = AgentStore(store.sessions)
    strategy = {
        "symbol": "BTC/USDT", "currency": "USDT", "allocated_capital": "100",
        "max_risk_per_trade_percent": "1", "max_daily_loss_percent": "3",
        "stop_loss_percent": "2",
        "entry_conditions": [{"kind": "price_cross", "indicator": "PRICE", "operator": "crosses_above", "value": "100", "timeframe": "1m", "symbol": "BTCUSDT"}],
        "exit_conditions": [{"kind": "price_cross", "indicator": "PRICE", "operator": "crosses_below", "value": "95", "timeframe": "1m", "symbol": "BTCUSDT"}],
        "allowed_order_types": ["limit"],
    }
    created = agents.create("test-user", "Persisted agent", strategy)
    assert created["state"] == "DRAFT"
    assert AgentStore(store.sessions).get(created["id"], "test-user") == created

    agents.transition(created["id"], "test-user", "VALIDATING", False)
    with pytest.raises(InvalidAgentTransition):
        agents.transition(created["id"], "test-user", "PAPER_TRADING", True)
    agents.transition(created["id"], "test-user", "AWAITING_APPROVAL", False)
    activated = agents.approve_strategy(created["id"], "test-user")
    assert activated["state"] == "PAPER_TRADING"
    assert activated["strategy_version"] == 2
    assert activated["approval_status"] == "APPROVED"
    with pytest.raises(InvalidAgentTransition, match="Live execution is disabled"):
        agents.transition(created["id"], "test-user", "ACTIVE", True)

    from sqlalchemy import select
    from app.models import AuditLog, StrategyRevision
    with store.sessions() as session:
        revisions = session.scalars(select(StrategyRevision).where(StrategyRevision.agent_id == created["id"]).order_by(StrategyRevision.version)).all()
        assert revisions[0].strategy == strategy and revisions[0].approval_status == "DRAFT"
        assert revisions[1].approval_status == "APPROVED" and revisions[1].approved_by_user_id == "test-user"
        assert len(session.scalars(select(AuditLog).where(AuditLog.agent_id == created["id"])).all()) == 4


def test_exchange_credential_envelope_authenticates_ciphertext_and_owner(monkeypatch):
    import base64
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(b"k" * 32).decode("ascii"))
    envelope = encrypt_credential("private-api-secret", user_id="user-a", credential_id="cred-1")
    assert "private-api-secret" not in envelope
    assert decrypt_credential(envelope, user_id="user-a", credential_id="cred-1") == "private-api-secret"
    with pytest.raises(CredentialEncryptionError):
        decrypt_credential(envelope, user_id="user-b", credential_id="cred-1")
    with pytest.raises(CredentialEncryptionError):
        decrypt_credential(envelope[:-2] + "AA", user_id="user-a", credential_id="cred-1")

