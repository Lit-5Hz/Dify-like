from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202606020001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This project reset intentionally does not preserve the old App.workflow_spec schema.
    op.execute(
        """
        DROP TABLE IF EXISTS
            run_steps,
            runs,
            messages,
            conversations,
            workflow_versions,
            workflows,
            knowledge_chunks,
            knowledge_documents,
            app_tools,
            knowledge_bases,
            model_credentials,
            apps,
            users
        CASCADE
        """
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("auth_token", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_auth_token"), "users", ["auth_token"], unique=True)

    op.create_table(
        "apps",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("owner_user_id", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model_provider", sa.String(length=80), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("model_credential_id", sa.String(length=120), nullable=False),
        sa.Column("model_base_url", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Integer(), nullable=False),
        sa.Column("top_p", sa.Integer(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "model_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("owner_user_id", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("owner_user_id", sa.String(length=120), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("app_id", sa.String(length=120), nullable=False),
        sa.Column("conversation_id", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("embedding_provider", sa.String(length=80), nullable=False),
        sa.Column("embedding_model", sa.String(length=120), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("embedding_credential_id", sa.String(length=120), nullable=False),
        sa.Column("embedding_base_url", sa.Text(), nullable=False),
        sa.Column("qdrant_collection", sa.String(length=160), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False),
        sa.Column("chunk_strategy", sa.String(length=32), nullable=False),
        sa.Column("enable_parent_child", sa.Boolean(), nullable=False),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("qdrant_collection"),
    )
    op.create_index(op.f("ix_knowledge_bases_owner_user_id"), "knowledge_bases", ["owner_user_id"], unique=False)
    op.create_index(op.f("ix_knowledge_bases_scope"), "knowledge_bases", ["scope"], unique=False)
    op.create_index(op.f("ix_knowledge_bases_app_id"), "knowledge_bases", ["app_id"], unique=False)
    op.create_index(op.f("ix_knowledge_bases_conversation_id"), "knowledge_bases", ["conversation_id"], unique=False)

    op.create_table(
        "knowledge_documents",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "workflows",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("app_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("draft_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_version_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflows_app_id"), "workflows", ["app_id"], unique=False)
    op.create_index(op.f("ix_workflows_published_version_id"), "workflows", ["published_version_id"], unique=False)

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("qdrant_point_id", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "workflow_versions",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("spec_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "version_number", name="uq_workflow_versions_workflow_version_number"),
    )
    op.create_index(op.f("ix_workflow_versions_workflow_id"), "workflow_versions", ["workflow_id"], unique=False)
    op.create_foreign_key(
        "fk_workflows_published_version_id_workflow_versions",
        "workflows",
        "workflow_versions",
        ["published_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("app_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("app_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("workflow_version_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("input_message_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("output_message_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_version_id"], ["workflow_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "run_steps",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("run_steps")
    op.drop_table("runs")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_constraint("fk_workflows_published_version_id_workflow_versions", "workflows", type_="foreignkey")
    op.drop_index(op.f("ix_workflow_versions_workflow_id"), table_name="workflow_versions")
    op.drop_table("workflow_versions")
    op.drop_table("knowledge_chunks")
    op.drop_index(op.f("ix_workflows_published_version_id"), table_name="workflows")
    op.drop_index(op.f("ix_workflows_app_id"), table_name="workflows")
    op.drop_table("workflows")
    op.drop_table("knowledge_documents")
    op.drop_index(op.f("ix_knowledge_bases_conversation_id"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_app_id"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_scope"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_owner_user_id"), table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
    op.drop_table("model_credentials")
    op.drop_table("apps")
    op.drop_index(op.f("ix_users_auth_token"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
