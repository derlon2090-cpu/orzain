"""Persist agent state, immutable strategy versions, and audit events."""
from alembic import op
import sqlalchemy as sa

revision = "0002_agents_and_strategies"
down_revision = "0001_paper_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "trading_agents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("strategy_version", sa.Integer(), nullable=False),
        sa.Column("paper_account_id", sa.String(36), sa.ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("strategy_version > 0", name="ck_agent_strategy_version_positive"),
        sa.UniqueConstraint("paper_account_id", name="uq_trading_agents_paper_account_id"),
    )
    op.create_index("ix_trading_agents_state", "trading_agents", ["state"])
    op.create_table(
        "strategy_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("trading_agents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_strategy_revision_version_positive"),
        sa.UniqueConstraint("agent_id", "version", name="uq_strategy_revision_agent_version"),
    )
    op.create_index("ix_strategy_revisions_agent_id", "strategy_revisions", ["agent_id"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("trading_agents.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_agent_id", "audit_logs", ["agent_id"])
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])


def downgrade():
    op.drop_index("ix_audit_logs_event_type", table_name="audit_logs")
    op.drop_index("ix_audit_logs_agent_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_strategy_revisions_agent_id", table_name="strategy_revisions")
    op.drop_table("strategy_revisions")
    op.drop_index("ix_trading_agents_state", table_name="trading_agents")
    op.drop_table("trading_agents")
