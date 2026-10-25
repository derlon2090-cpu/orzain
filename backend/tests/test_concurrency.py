"""Optional integration check for PostgreSQL row-lock behavior.

Set TEST_DATABASE_URL to an isolated, already migrated PostgreSQL database to run.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
from uuid import uuid4

import pytest

from app.paper_store import PaperLedgerStore
from app.trading_models import OrderSide, OrderType, ProposedOrder
from app.models import User


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL via TEST_DATABASE_URL")
def test_concurrent_orders_cannot_spend_the_same_account_balance():
    store = PaperLedgerStore.for_url(os.environ["TEST_DATABASE_URL"])
    user_id = str(uuid4())
    with store.sessions() as session, session.begin():
        session.add(User(id=user_id, email=f"pg-{user_id}@example.test", password_hash="!", is_active=True, email_verified=True))
    account = store.create_account(user_id, Decimal("60"))

    def submit(key: str):
        order = ProposedOrder(
            agent_id="pg-concurrency", symbol="BTC/USDT", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=Decimal("0.5"),
            price=Decimal("100"), stop_loss_price=Decimal("95"),
        )
        return store.place_order(account["id"], order, key)

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(submit, ["pg-race-a", "pg-race-b"]))
        assert sum(result["status"] == "FILLED" for result in results) == 1
        assert sum(result["status"] == "REJECTED" for result in results) == 1
        account_state = store.get_account(account["id"])
        assert Decimal(account_state["available_balance"]) >= 0
        assert Decimal(account_state["reserved_balance"]) == 0
        assert account_state["status"] == "ACTIVE"
        assert store.verify_ledger(account["id"])["balanced"] is True
    finally:
        # Keep no test data; the URL must point to an isolated test database.
        from sqlalchemy import delete
        from app.models import LedgerEntry, LedgerTransaction, PaperOrder, PaperPosition, PaperAccount, User
        with store.sessions() as session, session.begin():
            session.execute(delete(LedgerEntry).where(LedgerEntry.account_id == account["id"]))
            session.execute(delete(LedgerTransaction).where(LedgerTransaction.account_id == account["id"]))
            session.execute(delete(PaperOrder).where(PaperOrder.account_id == account["id"]))
            session.execute(delete(PaperPosition).where(PaperPosition.account_id == account["id"]))
            session.execute(delete(PaperAccount).where(PaperAccount.id == account["id"]))
            session.execute(delete(User).where(User.id == user_id))
