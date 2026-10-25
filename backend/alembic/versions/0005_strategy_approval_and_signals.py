"""Explicit strategy approvals and durable evaluator state."""
from alembic import op
import sqlalchemy as sa

revision = "0005_strategy_approval_and_signals"
down_revision = "0004_password_recovery"
branch_labels = None
depends_on = None


def upgrade():
    # Batch mode keeps the FK column portable to SQLite smoke-test databases.
    with op.batch_alter_table("strategy_revisions") as batch:
        batch.add_column(sa.Column("approval_status", sa.String(16), nullable=False, server_default="DRAFT"))
        batch.add_column(sa.Column("approved_by_user_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_strategy_revisions_approved_by_user_id_users",
            "users", ["approved_by_user_id"], ["id"], ondelete="RESTRICT",
        )
        batch.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_strategy_revisions_approval_status", "strategy_revisions", ["approval_status"])
    op.create_table(
        "agent_signal_states",
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("trading_agents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_event_id", sa.String(64), nullable=True),
        sa.Column("last_status", sa.String(32), nullable=False, server_default="IDLE"),
        sa.Column("last_signal", sa.String(8), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("agent_signal_states")
    op.drop_index("ix_strategy_revisions_approval_status", table_name="strategy_revisions")
    with op.batch_alter_table("strategy_revisions") as batch:
        batch.drop_column("approved_at")
        batch.drop_column("approved_by_user_id")
        batch.drop_column("approval_status")
