from alembic import op
import sqlalchemy as sa
revision = "0006_upload_sessions"
down_revision = "0005_practical_auth_accounts"
branch_labels = None
depends_on = None
def upgrade():
    if "practical_upload_sessions" in sa.inspect(op.get_bind()).get_table_names(): return
    op.create_table("practical_upload_sessions", sa.Column("id",sa.String(36),primary_key=True), sa.Column("owner_id",sa.String(36),sa.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False), sa.Column("purpose",sa.String(40),nullable=False), sa.Column("filename",sa.String(255),nullable=False), sa.Column("size",sa.Integer(),nullable=False), sa.Column("sha256",sa.String(64),nullable=False), sa.Column("storage_key",sa.String(700),nullable=False), sa.Column("upload_url",sa.String(2000),nullable=False), sa.Column("status",sa.String(20),nullable=False,server_default="initiated"), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False))
    op.create_index("ix_practical_upload_sessions_owner_id","practical_upload_sessions",["owner_id"])
    op.create_index("uq_practical_upload_sessions_storage_key","practical_upload_sessions",["storage_key"],unique=True)
def downgrade(): op.drop_table("practical_upload_sessions")
