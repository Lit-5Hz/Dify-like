from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202606120001"
down_revision = "202606110002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_skills",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("owner_user_id", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("source_app_id", sa.String(length=120), nullable=False),
        sa.Column("source_workflow_id", sa.String(length=120), nullable=False),
        sa.Column("source_run_id", sa.String(length=120), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_platform_skills_owner_user_id"), "platform_skills", ["owner_user_id"], unique=False)
    op.create_index(op.f("ix_platform_skills_status"), "platform_skills", ["status"], unique=False)
    op.create_index(op.f("ix_platform_skills_source_app_id"), "platform_skills", ["source_app_id"], unique=False)
    op.create_index(op.f("ix_platform_skills_source_workflow_id"), "platform_skills", ["source_workflow_id"], unique=False)
    op.create_index(op.f("ix_platform_skills_source_run_id"), "platform_skills", ["source_run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_platform_skills_source_run_id"), table_name="platform_skills")
    op.drop_index(op.f("ix_platform_skills_source_workflow_id"), table_name="platform_skills")
    op.drop_index(op.f("ix_platform_skills_source_app_id"), table_name="platform_skills")
    op.drop_index(op.f("ix_platform_skills_status"), table_name="platform_skills")
    op.drop_index(op.f("ix_platform_skills_owner_user_id"), table_name="platform_skills")
    op.drop_table("platform_skills")
