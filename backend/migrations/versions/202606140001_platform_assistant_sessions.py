from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202606140001"
down_revision = "202606120001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_assistant_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("owner_user_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("messages_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("draft_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_app_id", sa.String(length=120), nullable=False),
        sa.Column("created_workflow_id", sa.String(length=120), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_platform_assistant_sessions_owner_user_id"), "platform_assistant_sessions", ["owner_user_id"], unique=False)
    op.create_index(op.f("ix_platform_assistant_sessions_status"), "platform_assistant_sessions", ["status"], unique=False)
    op.create_index(op.f("ix_platform_assistant_sessions_created_app_id"), "platform_assistant_sessions", ["created_app_id"], unique=False)
    op.create_index(op.f("ix_platform_assistant_sessions_created_workflow_id"), "platform_assistant_sessions", ["created_workflow_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_platform_assistant_sessions_created_workflow_id"), table_name="platform_assistant_sessions")
    op.drop_index(op.f("ix_platform_assistant_sessions_created_app_id"), table_name="platform_assistant_sessions")
    op.drop_index(op.f("ix_platform_assistant_sessions_status"), table_name="platform_assistant_sessions")
    op.drop_index(op.f("ix_platform_assistant_sessions_owner_user_id"), table_name="platform_assistant_sessions")
    op.drop_table("platform_assistant_sessions")
