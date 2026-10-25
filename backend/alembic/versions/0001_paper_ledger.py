"""Initial durable paper accounts, orders, positions, and double-entry ledger."""
from alembic import op
import sqlalchemy as sa

revision = "0001_paper_ledger"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("available_balance", sa.Numeric(24, 8), nullable=False),
        sa.Column("reserved_balance", sa.Numeric(24, 8), nullable=False),
        sa.Column("daily_net_pnl", sa.Numeric(24, 8), nullable=False),
        sa.Column("daily_pnl_date", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("available_balance >= 0", name="ck_paper_account_available_nonnegative"),
        sa.CheckConstraint("reserved_balance >= 0", name="ck_paper_account_reserved_nonnegative"),
    )
    op.create_table(
        "paper_orders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("order_type", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("reference_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("executed_price", sa.Numeric(24, 8)),
        sa.Column("fee", sa.Numeric(24, 8)),
        sa.Column("reserved_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rejection_code", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("account_id", "idempotency_key", name="uq_paper_order_account_idempotency"),
        sa.CheckConstraint("quantity > 0", name="ck_paper_order_quantity_positive"),
        sa.CheckConstraint("reference_price > 0", name="ck_paper_order_price_positive"),
        sa.CheckConstraint("reserved_amount >= 0", name="ck_paper_order_reserved_nonnegative"),
    )
    op.create_index("ix_paper_orders_account_id", "paper_orders", ["account_id"])
    op.create_index("ix_paper_orders_status", "paper_orders", ["status"])

    op.create_table(
        "paper_positions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("cost_basis", sa.Numeric(24, 8), nullable=False),
        sa.UniqueConstraint("account_id", "symbol", name="uq_paper_position_account_symbol"),
        sa.CheckConstraint("quantity >= 0", name="ck_paper_position_quantity_nonnegative"),
        sa.CheckConstraint("cost_basis >= 0", name="ck_paper_position_cost_basis_nonnegative"),
    )
    op.create_table(
        "ledger_transactions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_key", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "source_key", name="uq_ledger_account_source"),
    )
    op.create_index("ix_ledger_transactions_account_id", "ledger_transactions", ["account_id"])
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("transaction_id", sa.String(length=36), sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("account_id", sa.String(length=36), sa.ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("account_code", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_ledger_entry_amount_positive"),
        sa.CheckConstraint("side IN ('DEBIT', 'CREDIT')", name="ck_ledger_entry_side"),
    )
    op.create_index("ix_ledger_entries_transaction_id", "ledger_entries", ["transaction_id"])
    op.create_index("ix_ledger_entries_account_id", "ledger_entries", ["account_id"])


def downgrade():
    op.drop_index("ix_ledger_entries_account_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_transaction_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_ledger_transactions_account_id", table_name="ledger_transactions")
    op.drop_table("ledger_transactions")
    op.drop_table("paper_positions")
    op.drop_index("ix_paper_orders_status", table_name="paper_orders")
    op.drop_index("ix_paper_orders_account_id", table_name="paper_orders")
    op.drop_table("paper_orders")
    op.drop_table("paper_accounts")
