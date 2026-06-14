from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202606140002"
down_revision = "202606140001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("platform_skills", sa.Column("visibility", sa.String(length=32), server_default="private", nullable=False))
    op.add_column("platform_skills", sa.Column("publish_status", sa.String(length=32), server_default="draft", nullable=False))
    op.add_column("platform_skills", sa.Column("source_skill_id", sa.String(length=120), server_default="", nullable=False))
    op.add_column("platform_skills", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("platform_skills", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("platform_skills", sa.Column("usage_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("platform_skills", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_platform_skills_visibility"), "platform_skills", ["visibility"], unique=False)
    op.create_index(op.f("ix_platform_skills_publish_status"), "platform_skills", ["publish_status"], unique=False)
    op.create_index(op.f("ix_platform_skills_source_skill_id"), "platform_skills", ["source_skill_id"], unique=False)

    op.create_table(
        "skill_search_documents",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.String(length=120), nullable=False),
        sa.Column("publish_status", sa.String(length=32), nullable=False),
        sa.Column("tokenizer", sa.String(length=32), nullable=False),
        sa.Column("index_version", sa.String(length=64), nullable=False),
        sa.Column("search_text_hash", sa.String(length=64), nullable=False),
        sa.Column("field_tokens_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("field_token_counts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("field_lengths_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("all_token_counts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("doc_length", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["platform_skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_skill_search_documents_skill_id"), "skill_search_documents", ["skill_id"], unique=False)
    op.create_index(op.f("ix_skill_search_documents_visibility"), "skill_search_documents", ["visibility"], unique=False)
    op.create_index(op.f("ix_skill_search_documents_owner_user_id"), "skill_search_documents", ["owner_user_id"], unique=False)
    op.create_index(op.f("ix_skill_search_documents_publish_status"), "skill_search_documents", ["publish_status"], unique=False)
    op.create_index(op.f("ix_skill_search_documents_index_version"), "skill_search_documents", ["index_version"], unique=False)

    op.create_table(
        "skill_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("owner_user_id", sa.String(length=120), nullable=False),
        sa.Column("assistant_session_id", sa.String(length=120), nullable=False),
        sa.Column("usage_stage", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["platform_skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_skill_usage_events_skill_id"), "skill_usage_events", ["skill_id"], unique=False)
    op.create_index(op.f("ix_skill_usage_events_owner_user_id"), "skill_usage_events", ["owner_user_id"], unique=False)
    op.create_index(op.f("ix_skill_usage_events_assistant_session_id"), "skill_usage_events", ["assistant_session_id"], unique=False)
    op.create_index(op.f("ix_skill_usage_events_usage_stage"), "skill_usage_events", ["usage_stage"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_skill_usage_events_usage_stage"), table_name="skill_usage_events")
    op.drop_index(op.f("ix_skill_usage_events_assistant_session_id"), table_name="skill_usage_events")
    op.drop_index(op.f("ix_skill_usage_events_owner_user_id"), table_name="skill_usage_events")
    op.drop_index(op.f("ix_skill_usage_events_skill_id"), table_name="skill_usage_events")
    op.drop_table("skill_usage_events")

    op.drop_index(op.f("ix_skill_search_documents_index_version"), table_name="skill_search_documents")
    op.drop_index(op.f("ix_skill_search_documents_publish_status"), table_name="skill_search_documents")
    op.drop_index(op.f("ix_skill_search_documents_owner_user_id"), table_name="skill_search_documents")
    op.drop_index(op.f("ix_skill_search_documents_visibility"), table_name="skill_search_documents")
    op.drop_index(op.f("ix_skill_search_documents_skill_id"), table_name="skill_search_documents")
    op.drop_table("skill_search_documents")

    op.drop_index(op.f("ix_platform_skills_source_skill_id"), table_name="platform_skills")
    op.drop_index(op.f("ix_platform_skills_publish_status"), table_name="platform_skills")
    op.drop_index(op.f("ix_platform_skills_visibility"), table_name="platform_skills")
    op.drop_column("platform_skills", "last_used_at")
    op.drop_column("platform_skills", "usage_count")
    op.drop_column("platform_skills", "revoked_at")
    op.drop_column("platform_skills", "published_at")
    op.drop_column("platform_skills", "source_skill_id")
    op.drop_column("platform_skills", "publish_status")
    op.drop_column("platform_skills", "visibility")
