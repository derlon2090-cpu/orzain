"""Users, sessions, tenant ownership, and exchange credential storage."""
from alembic import op
import sqlalchemy as sa

revision = "0003_users_and_access_control"
down_revision = "0002_agents_and_strategies"
branch_labels = None
depends_on = None

LEGACY_OWNER_ID = "00000000-0000-4000-8000-000000000003"


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_email_verification_tokens_token_hash"),
    )
    op.create_index("ix_email_verification_tokens_user_id", "email_verification_tokens", ["user_id"])
    op.create_table(
        "exchange_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "exchange", "label", name="uq_exchange_credential_user_exchange_label"),
    )
    op.create_index("ix_exchange_credentials_user_id", "exchange_credentials", ["user_id"])

    # Existing demo data is retained under an inactive, non-login-capable owner.
    op.execute(sa.text(
        "INSERT INTO users (id, email, password_hash, is_active, email_verified, created_at) "
        "VALUES (:id, :email, :password_hash, :active, :verified, CURRENT_TIMESTAMP)"
    ).bindparams(id=LEGACY_OWNER_ID, email="legacy-owner@invalid.local", password_hash="!disabled!", active=False, verified=False))
    op.add_column("paper_accounts", sa.Column("user_id", sa.String(36), nullable=True))
    op.add_column("trading_agents", sa.Column("user_id", sa.String(36), nullable=True))
    op.execute(sa.text("UPDATE paper_accounts SET user_id = :owner").bindparams(owner=LEGACY_OWNER_ID))
    op.execute(sa.text("UPDATE trading_agents SET user_id = :owner").bindparams(owner=LEGACY_OWNER_ID))
    with op.batch_alter_table("paper_accounts") as batch:
        batch.alter_column("user_id", existing_type=sa.String(36), nullable=False)
        batch.create_foreign_key("fk_paper_accounts_user_id_users", "users", ["user_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_paper_accounts_user_id", ["user_id"])
    with op.batch_alter_table("trading_agents") as batch:
        batch.alter_column("user_id", existing_type=sa.String(36), nullable=False)
        batch.create_foreign_key("fk_trading_agents_user_id_users", "users", ["user_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_trading_agents_user_id", ["user_id"])


def downgrade():
    raise RuntimeError("Downgrade would erase tenant ownership and authentication data; restore a backup instead.")
