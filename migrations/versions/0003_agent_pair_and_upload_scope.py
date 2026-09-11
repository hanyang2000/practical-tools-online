"""Persist one-time Agent pairing codes and bind upload batches."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_agent_pair_and_upload_scope"
down_revision = "0002_analysis_owner_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "practical_capture_pair_codes" not in tables:
        op.create_table(
            "practical_capture_pair_codes",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("code_hash", sa.String(length=64), nullable=False),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_practical_pair_codes_created_by_users", ondelete="SET NULL"),
            sa.UniqueConstraint("code_hash", name="uq_practical_capture_pair_code_hash"),
        )
        op.create_index("ix_practical_capture_pair_codes_code_hash", "practical_capture_pair_codes", ["code_hash"])
        op.create_index("ix_practical_capture_pair_codes_created_by", "practical_capture_pair_codes", ["created_by"])
        op.create_index("ix_practical_capture_pair_codes_expires_at", "practical_capture_pair_codes", ["expires_at"])

    upload_columns = {column["name"] for column in inspect(connection).get_columns("practical_capture_uploads")}
    if "owner_id" not in upload_columns:
        with op.batch_alter_table("practical_capture_uploads", recreate="always") as batch:
            batch.add_column(sa.Column("owner_id", sa.String(length=36), sa.ForeignKey("users.id", name="fk_practical_uploads_owner_id_users", ondelete="SET NULL"), nullable=True))
            batch.add_column(sa.Column("agent_id", sa.String(length=120), sa.ForeignKey("practical_capture_agents.device_id", name="fk_practical_uploads_agent_id_agents", ondelete="SET NULL"), nullable=True))
            batch.create_index("ix_practical_capture_uploads_owner_id", ["owner_id"])
            batch.create_index("ix_practical_capture_uploads_agent_id", ["agent_id"])


def downgrade() -> None:
    connection = op.get_bind()
    if "practical_capture_uploads" in inspect(connection).get_table_names():
        with op.batch_alter_table("practical_capture_uploads", recreate="always") as batch:
            batch.drop_index("ix_practical_capture_uploads_agent_id")
            batch.drop_index("ix_practical_capture_uploads_owner_id")
            batch.drop_column("agent_id")
            batch.drop_column("owner_id")
    if "practical_capture_pair_codes" in inspect(connection).get_table_names():
        op.drop_index("ix_practical_capture_pair_codes_expires_at", table_name="practical_capture_pair_codes")
        op.drop_index("ix_practical_capture_pair_codes_created_by", table_name="practical_capture_pair_codes")
        op.drop_index("ix_practical_capture_pair_codes_code_hash", table_name="practical_capture_pair_codes")
        op.drop_table("practical_capture_pair_codes")
