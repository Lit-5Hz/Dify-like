from __future__ import annotations

from alembic import op


revision = "202606030001"
down_revision = "202606020001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_tools")


def downgrade() -> None:
    pass
