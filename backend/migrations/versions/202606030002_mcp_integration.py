from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202606030002"
down_revision = "202606030001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("owner_user_id", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("transport_type", sa.String(length=32), nullable=False),
        sa.Column("server_url", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.String(length=32), nullable=False),
        sa.Column("encrypted_auth_secret", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=False),
        sa.Column("tool_manifest_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_external_mcp_servers_owner_user_id"), "external_mcp_servers", ["owner_user_id"], unique=False)

    op.create_table(
        "workflow_mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("server_name", sa.String(length=120), nullable=False),
        sa.Column("server_slug", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.String(length=32), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id"),
        sa.UniqueConstraint("server_slug"),
    )


def downgrade() -> None:
    op.drop_table("workflow_mcp_servers")
    op.drop_index(op.f("ix_external_mcp_servers_owner_user_id"), table_name="external_mcp_servers")
    op.drop_table("external_mcp_servers")
