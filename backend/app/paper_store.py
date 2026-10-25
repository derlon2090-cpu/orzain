"""Transactional paper accounts, idempotent commands, and double-entry ledger."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
import anyio

from app.db import Base, make_engine, SessionLocal
from app.execution import BaseExecutionAdapter
from app.models import LedgerEntry, LedgerTransaction, PaperAccount, PaperOrder, PaperPosition
from app.trading_models import ProposedOrder

MONEY = Decimal("0.00000001")


class IdempotencyConflict(Exception):
    """The same idempotency key was reused for a different order payload."""


class AccountUnavailable(Exception):
    pass


class PaperLedgerStore:
    def __init__(self, session_factory: sessionmaker = SessionLocal, *, fee_rate: Decimal = Decimal("0.001"), slippage_rate: Decimal = Decimal("0.0005")):
        self.sessions = session_factory
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate

    @classmethod
    def for_url(cls, url: str, *, create_schema: bool = False):
        engine = make_engine(url)
        if create_schema:
            Base.metadata.create_all(engine)
        return cls(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))

    @staticmethod
    def _fingerprint(order: ProposedOrder) -> str:
        data = {
            "agent_id": order.agent_id, "symbol": order.symbol, "side": order.side.value,
            "order_type": order.order_type.value, "quantity": str(order.quantity),
            "price": str(order.price), "stop_loss_price": str(order.stop_loss_price),
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _amount(value: Decimal) -> Decimal:
        return value.quantize(MONEY, rounding=ROUND_HALF_EVEN)

    @staticmethod
    def _write_ledger(session, account: PaperAccount, source_key: str, kind: str, postings: list[tuple[str, str, Decimal]]):
        postings = [(code, side, PaperLedgerStore._amount(amount)) for code, side, amount in postings if amount != 0]
        if not postings:
            return None
        debits = sum((amount for _, side, amount in postings if side == "DEBIT"), Decimal("0"))
        credits = sum((amount for _, side, amount in postings if side == "CREDIT"), Decimal("0"))
        if debits != credits:
            raise ArithmeticError(f"Unbalanced ledger transaction: debits={debits} credits={credits}")
        tx = LedgerTransaction(account_id=account.id, source_key=source_key, kind=kind)
        session.add(tx)
        session.flush()
        for code, side, amount in postings:
            session.add(LedgerEntry(transaction_id=tx.id, account_id=account.id, account_code=code, side=side, amount=amount, currency=account.currency))
        return tx

    @staticmethod
    def _order_response(order: PaperOrder) -> dict:
        return {
            "order_id": order.id, "account_id": order.account_id,
            "agent_id": order.agent_id,
            "idempotency_key": order.idempotency_key, "symbol": order.symbol,
            "side": order.side, "order_type": order.order_type,
            "quantity": str(order.quantity), "reference_price": str(order.reference_price),
            "executed_price": str(order.executed_price) if order.executed_price is not None else None,
            "fee": str(order.fee) if order.fee is not None else None,
            "status": order.status, "rejection_code": order.rejection_code,
        }

    def create_account(self, user_id: str, initial_balance: Decimal, currency: str = "USDT") -> dict:
        if initial_balance < 0:
            raise ValueError("Initial balance cannot be negative")
        initial_balance = self._amount(initial_balance)
        with self.sessions() as session, session.begin():
            account = PaperAccount(user_id=user_id, currency=currency, available_balance=initial_balance, reserved_balance=Decimal("0"), status="ACTIVE")
            session.add(account)
            session.flush()
            if initial_balance:
                self._write_ledger(session, account, f"funding:{account.id}", "INITIAL_FUNDING", [
                    ("CASH_AVAILABLE", "DEBIT", initial_balance),
                    ("PAPER_CAPITAL", "CREDIT", initial_balance),
                ])
            session.flush()
            return self._account_response(account)

    @staticmethod
    def _account_response(account: PaperAccount) -> dict:
        today = datetime.now(timezone.utc).date().isoformat()
        pnl = account.daily_net_pnl if account.daily_pnl_date == today else Decimal("0")
        return {
            "id": account.id, "currency": account.currency,
            "available_balance": str(account.available_balance),
            "reserved_balance": str(account.reserved_balance), "status": account.status,
            "daily_loss": str(max(Decimal("0"), -pnl)),
        }

    def get_account(self, account_id: str) -> dict | None:
        with self.sessions() as session:
            account = session.get(PaperAccount, account_id)
            return self._account_response(account) if account else None

    def _duplicate(self, session, account_id: str, key: str, fingerprint: str):
        existing = session.scalar(select(PaperOrder).where(PaperOrder.account_id == account_id, PaperOrder.idempotency_key == key))
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyConflict("Idempotency key was already used with a different order payload")
            return self._order_response(existing)
        return None

    def get_order_by_idempotency(self, account_id: str, key: str, order: ProposedOrder) -> dict | None:
        with self.sessions() as session:
            return self._duplicate(session, account_id, key, self._fingerprint(order))

    def record_rejection(self, account_id: str, order: ProposedOrder, key: str, rejection_code: str) -> dict:
        fingerprint = self._fingerprint(order)
        try:
            with self.sessions() as session, session.begin():
                duplicate = self._duplicate(session, account_id, key, fingerprint)
                if duplicate:
                    return duplicate
                account = session.get(PaperAccount, account_id)
                if account is None:
                    raise KeyError("Paper account not found")
                record = PaperOrder(
                    account_id=account_id, idempotency_key=key, request_fingerprint=fingerprint,
                    agent_id=order.agent_id, symbol=order.symbol, side=order.side.value,
                    order_type=order.order_type.value, quantity=order.quantity,
                    reference_price=order.price, reserved_amount=Decimal("0"),
                    status="REJECTED", rejection_code=rejection_code,
                )
                session.add(record)
                session.flush()
                return self._order_response(record)
        except IntegrityError:
            with self.sessions() as session:
                duplicate = self._duplicate(session, account_id, key, fingerprint)
                if duplicate:
                    return duplicate
            raise

    def place_order(self, account_id: str, order: ProposedOrder, idempotency_key: str, *, fail_after_record: bool = False) -> dict:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("A 1–128 character idempotency key is required")
        fingerprint = self._fingerprint(order)
        direction = Decimal("1") if order.side.value == "BUY" else Decimal("-1")
        execution_price = self._amount(order.price * (Decimal("1") + direction * self.slippage_rate))
        gross = self._amount(order.quantity * execution_price)
        fee = self._amount(gross * self.fee_rate)
        reserve = gross + fee if order.side.value == "BUY" else Decimal("0")
        pending_id = None

        # Commit durable intent and reservation before simulation. On a crash, the account stays fenced.
        try:
            with self.sessions() as session, session.begin():
                account = session.scalar(select(PaperAccount).where(PaperAccount.id == account_id).with_for_update())
                if account is None:
                    raise KeyError("Paper account not found")
                duplicate = self._duplicate(session, account_id, idempotency_key, fingerprint)
                if duplicate:
                    return duplicate
                status, rejection = "PENDING_RECONCILIATION", None
                position = session.scalar(select(PaperPosition).where(PaperPosition.account_id == account_id, PaperPosition.symbol == order.symbol).with_for_update())
                if account.status != "ACTIVE":
                    status, rejection = "REJECTED", "ACCOUNT_RECONCILIATION_REQUIRED"
                elif order.side.value == "BUY" and account.available_balance < reserve:
                    status, rejection = "REJECTED", "INSUFFICIENT_PAPER_FUNDS"
                elif order.side.value == "SELL" and (position is None or position.quantity < order.quantity):
                    status, rejection = "REJECTED", "INSUFFICIENT_PAPER_POSITION"
                record = PaperOrder(
                    account_id=account_id, idempotency_key=idempotency_key, request_fingerprint=fingerprint,
                    agent_id=order.agent_id, symbol=order.symbol, side=order.side.value,
                    order_type=order.order_type.value, quantity=order.quantity,
                    reference_price=order.price, reserved_amount=reserve if rejection is None else Decimal("0"),
                    status=status, rejection_code=rejection,
                )
                session.add(record)
                session.flush()
                if rejection is None:
                    account.status = "RECONCILIATION_REQUIRED"
                    if order.side.value == "BUY":
                        account.available_balance -= reserve
                        account.reserved_balance += reserve
                        self._write_ledger(session, account, f"order:{record.id}:reserve", "RESERVATION", [
                            ("CASH_RESERVED", "DEBIT", reserve),
                            ("CASH_AVAILABLE", "CREDIT", reserve),
                        ])
                session.flush()
                pending_id = record.id
        except IntegrityError:
            # A concurrent request may have won the unique(account_id, key) insert race.
            with self.sessions() as session:
                duplicate = self._duplicate(session, account_id, idempotency_key, fingerprint)
                if duplicate:
                    return duplicate
            raise

        if status == "REJECTED":
            with self.sessions() as session:
                return self._order_response(session.get(PaperOrder, pending_id))
        if fail_after_record:
            with self.sessions() as session:
                return self._order_response(session.get(PaperOrder, pending_id))
        return self.reconcile_order(pending_id)

    def reconcile_order(self, order_id: str) -> dict:
        """Resolve a persisted paper intent exactly once; safe to rerun after process restart."""
        with self.sessions() as session, session.begin():
            order = session.scalar(select(PaperOrder).where(PaperOrder.id == order_id).with_for_update())
            if order is None:
                raise KeyError("Paper order not found")
            if order.status != "PENDING_RECONCILIATION":
                return self._order_response(order)
            account = session.scalar(select(PaperAccount).where(PaperAccount.id == order.account_id).with_for_update())
            position = session.scalar(select(PaperPosition).where(PaperPosition.account_id == account.id, PaperPosition.symbol == order.symbol).with_for_update())
            direction = Decimal("1") if order.side == "BUY" else Decimal("-1")
            executed_price = self._amount(order.reference_price * (Decimal("1") + direction * self.slippage_rate))
            gross = self._amount(order.quantity * executed_price)
            fee = self._amount(gross * self.fee_rate)

            if order.side == "BUY":
                if position is None:
                    position = PaperPosition(account_id=account.id, symbol=order.symbol, quantity=Decimal("0"), cost_basis=Decimal("0"))
                    session.add(position)
                    session.flush()
                position.quantity += order.quantity
                position.cost_basis += gross
                account.reserved_balance -= order.reserved_amount
                self._write_ledger(session, account, f"order:{order.id}:fill", "PAPER_FILL", [
                    ("PAPER_ASSET", "DEBIT", gross), ("CASH_RESERVED", "CREDIT", gross),
                    ("FEE_EXPENSE", "DEBIT", fee), ("CASH_RESERVED", "CREDIT", fee),
                ])
            else:
                if position is None or position.quantity < order.quantity:
                    # Do not silently settle an impossible sell. Leave funds/account fenced for review.
                    return self._order_response(order)
                cost = self._amount(position.cost_basis * order.quantity / position.quantity)
                position.quantity -= order.quantity
                position.cost_basis -= cost
                net_before_fee = gross
                pnl = gross - cost
                sale_postings = [("CASH_AVAILABLE", "DEBIT", net_before_fee), ("PAPER_ASSET", "CREDIT", cost)]
                if pnl > 0:
                    sale_postings.append(("REALIZED_GAIN", "CREDIT", pnl))
                elif pnl < 0:
                    sale_postings.append(("REALIZED_LOSS", "DEBIT", -pnl))
                sale_postings.extend([("FEE_EXPENSE", "DEBIT", fee), ("CASH_AVAILABLE", "CREDIT", fee)])
                self._write_ledger(session, account, f"order:{order.id}:fill", "PAPER_FILL", sale_postings)
                account.available_balance += gross - fee
                today = datetime.now(timezone.utc).date().isoformat()
                if account.daily_pnl_date != today:
                    account.daily_net_pnl = Decimal("0")
                    account.daily_pnl_date = today
                account.daily_net_pnl += pnl - fee
            if order.side == "BUY":
                # Fee is expensed at fill; include it in the account's day result even though no sale occurred.
                today = datetime.now(timezone.utc).date().isoformat()
                if account.daily_pnl_date != today:
                    account.daily_net_pnl = Decimal("0")
                    account.daily_pnl_date = today
                account.daily_net_pnl -= fee
            order.executed_price = executed_price
            order.fee = fee
            order.status = "FILLED"
            order.resolved_at = datetime.now(timezone.utc)
            account.status = "ACTIVE"
            session.flush()
            return self._order_response(order)

    def list_orders(self, account_id: str | None = None, *, user_id: str | None = None) -> list[dict]:
        with self.sessions() as session:
            query = select(PaperOrder).join(PaperAccount, PaperOrder.account_id == PaperAccount.id).order_by(PaperOrder.created_at.desc())
            if account_id:
                query = query.where(PaperOrder.account_id == account_id)
            if user_id:
                query = query.where(PaperAccount.user_id == user_id)
            return [self._order_response(item) for item in session.scalars(query)]

    def verify_ledger(self, account_id: str) -> dict:
        """Report every ledger transaction whose debit and credit totals differ."""
        with self.sessions() as session:
            tx_ids = session.scalars(select(LedgerTransaction.id).where(LedgerTransaction.account_id == account_id)).all()
            unbalanced = []
            for tx_id in tx_ids:
                totals = session.execute(select(LedgerEntry.side, func.sum(LedgerEntry.amount)).where(LedgerEntry.transaction_id == tx_id).group_by(LedgerEntry.side)).all()
                amounts = {side: Decimal(total) for side, total in totals}
                if amounts.get("DEBIT", Decimal("0")) != amounts.get("CREDIT", Decimal("0")):
                    unbalanced.append(tx_id)
            return {"account_id": account_id, "transaction_count": len(tx_ids), "unbalanced_transaction_ids": unbalanced, "balanced": not unbalanced}


class PersistentPaperExecutionAdapter(BaseExecutionAdapter):
    def __init__(self, store: PaperLedgerStore, account_id: str):
        self.store = store
        self.account_id = account_id

    async def place_order(self, order: ProposedOrder, idempotency_key: str) -> dict:
        return await anyio.to_thread.run_sync(self.store.place_order, self.account_id, order, idempotency_key)

    async def cancel_order(self, order_id: str) -> bool:
        return False  # This simulator resolves accepted orders immediately.
