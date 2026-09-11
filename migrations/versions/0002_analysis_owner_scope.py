"""Add account ownership to analysis daily rows and manual fixes.

Revision 0001 created these tables before account isolation was wired through
the analysis compatibility layer.  Keep this as a separate revision so an
already-initialized production database can be upgraded safely; fresh installs
get the same columns from the current SQLAlchemy metadata in 0001.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0002_analysis_owner_scope"
down_revision = "0001_practical_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode is required for SQLite (used by the contract tests), while
    # remaining valid for PostgreSQL production upgrades.
    connection = op.get_bind()
    if "owner_id" not in {column["name"] for column in inspect(connection).get_columns("practical_analysis_daily")}:
        with op.batch_alter_table("practical_analysis_daily", recreate="always") as batch:
            batch.add_column(
                sa.Column(
                    "owner_id",
                    sa.String(length=36),
                    sa.ForeignKey("users.id", name="fk_practical_analysis_daily_owner_id_users", ondelete="SET NULL"),
                    nullable=True,
                )
            )
            batch.drop_constraint("uq_practical_analysis_daily", type_="unique")
            batch.create_unique_constraint(
                "uq_practical_analysis_daily_owner", ["owner_id", "year", "date"]
            )
            batch.create_index("ix_practical_analysis_daily_owner_id", ["owner_id"])

    if "owner_id" not in {column["name"] for column in inspect(connection).get_columns("practical_analysis_fix")}:
        with op.batch_alter_table("practical_analysis_fix", recreate="always") as batch:
            batch.add_column(
                sa.Column(
                    "owner_id",
                    sa.String(length=36),
                    sa.ForeignKey("users.id", name="fk_practical_analysis_fix_owner_id_users", ondelete="SET NULL"),
                    nullable=True,
                )
            )
            batch.drop_constraint("uq_practical_analysis_fix", type_="unique")
            batch.create_unique_constraint(
                "uq_practical_analysis_fix_owner", ["owner_id", "year", "date", "field"]
            )
            batch.create_index("ix_practical_analysis_fix_owner_id", ["owner_id"])


def downgrade() -> None:
    with op.batch_alter_table("practical_analysis_fix", recreate="always") as batch:
        batch.drop_index("ix_practical_analysis_fix_owner_id")
        batch.drop_constraint("uq_practical_analysis_fix_owner", type_="unique")
        batch.create_unique_constraint(
            "uq_practical_analysis_fix", ["year", "date", "field"]
        )
        batch.drop_column("owner_id")

    with op.batch_alter_table("practical_analysis_daily", recreate="always") as batch:
        batch.drop_index("ix_practical_analysis_daily_owner_id")
        batch.drop_constraint("uq_practical_analysis_daily_owner", type_="unique")
        batch.create_unique_constraint("uq_practical_analysis_daily", ["year", "date"])
        batch.drop_column("owner_id")
