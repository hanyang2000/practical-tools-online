"""Add administrator-managed migration task records."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0004_admin_migration_tasks"
down_revision = "0003_agent_pair_and_upload_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "practical_migration_tasks" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "practical_migration_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("bundle_path", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=True),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_practical_migration_tasks_status", "practical_migration_tasks", ["status"])
    op.create_index("ix_practical_migration_tasks_owner_id", "practical_migration_tasks", ["owner_id"])


def downgrade() -> None:
    if "practical_migration_tasks" not in inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("ix_practical_migration_tasks_owner_id", table_name="practical_migration_tasks")
    op.drop_index("ix_practical_migration_tasks_status", table_name="practical_migration_tasks")
    op.drop_table("practical_migration_tasks")
