from alembic import op
import sqlalchemy as sa

revision = "0007_capture_job_claim_index"
down_revision = "0006_upload_sessions"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_indexes("practical_capture_jobs")}
    if "ix_practical_capture_jobs_claim" not in existing:
        op.create_index(
            "ix_practical_capture_jobs_claim",
            "practical_capture_jobs",
            ["status", "owner_id", "agent_id", "created_at"],
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_indexes("practical_capture_jobs")}
    if "ix_practical_capture_jobs_claim" in existing:
        op.drop_index("ix_practical_capture_jobs_claim", table_name="practical_capture_jobs")
