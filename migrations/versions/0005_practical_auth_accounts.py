"""Add application-local credentials without touching shared auth tables."""
from alembic import op
import sqlalchemy as sa

revision = "0005_practical_auth_accounts"
down_revision = "0004_admin_migration_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "practical_auth_accounts" not in inspector.get_table_names():
      op.create_table(
        "practical_auth_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="planner"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("avatar_storage_key", sa.String(500)),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("setup_marker", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
      )
      op.create_index("ix_practical_auth_accounts_username", "practical_auth_accounts", ["username"], unique=True)
      op.create_index("ix_practical_auth_accounts_owner_id", "practical_auth_accounts", ["owner_id"])
      op.create_index("uq_practical_auth_accounts_setup_marker", "practical_auth_accounts", ["setup_marker"], unique=True)
    elif "setup_marker" not in {c["name"] for c in inspector.get_columns("practical_auth_accounts")}: 
      op.add_column("practical_auth_accounts", sa.Column("setup_marker", sa.String(40), nullable=True))
      op.create_index("uq_practical_auth_accounts_setup_marker", "practical_auth_accounts", ["setup_marker"], unique=True)
    if "practical_auth_sessions" not in inspector.get_table_names():
      op.create_table(
        "practical_auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("practical_auth_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
      )
      op.create_index("ix_practical_auth_sessions_account_id", "practical_auth_sessions", ["account_id"])
      op.create_index("ix_practical_auth_sessions_token_hash", "practical_auth_sessions", ["token_hash"], unique=True)
      op.create_index("ix_practical_auth_sessions_expires_at", "practical_auth_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("practical_auth_sessions")
    op.drop_table("practical_auth_accounts")
