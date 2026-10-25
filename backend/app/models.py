"""Authoritative account, paper order, and balanced ledger records."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PaperAccount(Base):
    __tablename__ = "paper_accounts"
    __table_args__ = (
        CheckConstraint("available_balance >= 0", name="ck_paper_account_available_nonnegative"),
        CheckConstraint("reserved_balance >= 0", name="ck_paper_account_reserved_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USDT")
    available_balance: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    reserved_balance: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=Decimal("0"))
    daily_net_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=Decimal("0"))
    daily_pnl_date: Mapped[str] = mapped_column(String(10), nullable=False, default=lambda: utc_now().date().isoformat())
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    orders: Mapped[list["PaperOrder"]] = relationship(back_populates="account")


class PaperOrder(Base):
    __tablename__ = "paper_orders"
    __table_args__ = (
        UniqueConstraint("account_id", "idempotency_key", name="uq_paper_order_account_idempotency"),
        CheckConstraint("quantity > 0", name="ck_paper_order_quantity_positive"),
        CheckConstraint("reference_price > 0", name="ck_paper_order_price_positive"),
        CheckConstraint("reserved_amount >= 0", name="ck_paper_order_reserved_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    executed_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    fee: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    reserved_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_RECONCILIATION", index=True)
    rejection_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account: Mapped[PaperAccount] = relationship(back_populates="orders")


class PaperPosition(Base):
    __tablename__ = "paper_positions"
    __table_args__ = (
        UniqueConstraint("account_id", "symbol", name="uq_paper_position_account_symbol"),
        CheckConstraint("quantity >= 0", name="ck_paper_position_quantity_nonnegative"),
        CheckConstraint("cost_basis >= 0", name="ck_paper_position_cost_basis_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=Decimal("0"))
    cost_basis: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=Decimal("0"))


class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"
    __table_args__ = (UniqueConstraint("account_id", "source_key", name="uq_ledger_account_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_key: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    entries: Mapped[list["LedgerEntry"]] = relationship(back_populates="transaction", cascade="all, delete-orphan")


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_ledger_entry_amount_positive"),
        CheckConstraint("side IN ('DEBIT', 'CREDIT')", name="ck_ledger_entry_side"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    account_code: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    transaction: Mapped[LedgerTransaction] = relationship(back_populates="entries")


class TradingAgent(Base):
    __tablename__ = "trading_agents"
    __table_args__ = (
        CheckConstraint("strategy_version > 0", name="ck_agent_strategy_version_positive"),
        UniqueConstraint("paper_account_id", name="uq_trading_agents_paper_account_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT", index=True)
    strategy_version: Mapped[int] = mapped_column(nullable=False, default=1)
    paper_account_id: Mapped[str] = mapped_column(ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    revisions: Mapped[list["StrategyRevision"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class StrategyRevision(Base):
    __tablename__ = "strategy_revisions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_strategy_revision_agent_version"),
        CheckConstraint("version > 0", name="ck_strategy_revision_version_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("trading_agents.id", ondelete="RESTRICT"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    strategy: Mapped[dict] = mapped_column(JSON, nullable=False)
    approval_status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT", index=True)
    approved_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    agent: Mapped[TradingAgent] = relationship(back_populates="revisions")


class AgentSignalState(Base):
    __tablename__ = "agent_signal_states"

    agent_id: Mapped[str] = mapped_column(ForeignKey("trading_agents.id", ondelete="CASCADE"), primary_key=True)
    last_event_id: Mapped[str | None] = mapped_column(String(64))
    last_status: Mapped[str] = mapped_column(String(32), nullable=False, default="IDLE")
    last_signal: Mapped[str | None] = mapped_column(String(8))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("trading_agents.id", ondelete="RESTRICT"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class ExchangeCredential(Base):
    __tablename__ = "exchange_credentials"
    __table_args__ = (UniqueConstraint("user_id", "exchange", "label", name="uq_exchange_credential_user_exchange_label"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
