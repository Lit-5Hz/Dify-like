from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "202606110001"
down_revision = "202606030002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("external_mcp_servers", sa.Column("encrypted_headers_json", sa.Text(), nullable=False, server_default=""))
    op.add_column("external_mcp_servers", sa.Column("mcp_session_id", sa.Text(), nullable=False, server_default=""))
    op.alter_column("external_mcp_servers", "encrypted_headers_json", server_default=None)
    op.alter_column("external_mcp_servers", "mcp_session_id", server_default=None)


def downgrade() -> None:
    op.drop_column("external_mcp_servers", "mcp_session_id")
    op.drop_column("external_mcp_servers", "encrypted_headers_json")
