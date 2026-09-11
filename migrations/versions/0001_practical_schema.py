"""Create practical-tools business tables.

The shared ``users`` and ``user_sessions`` tables are deliberately excluded;
they belong to 主图一条龙服务协同版 and are only reflected by the application.
"""
from alembic import op

from app.db import Base
from app import models  # noqa: F401

revision = "0001_practical_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in Base.metadata.sorted_tables:
        if table.name.startswith("practical_"):
            table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(Base.metadata.sorted_tables):
        if table.name.startswith("practical_"):
            table.drop(bind=bind, checkfirst=True)
