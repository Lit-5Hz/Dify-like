from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "202606110002"
down_revision = "202606110001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("external_mcp_servers", sa.Column("oauth_authorization_url", sa.Text(), nullable=False, server_default=""))
    op.add_column("external_mcp_servers", sa.Column("oauth_token_url", sa.Text(), nullable=False, server_default=""))
    op.add_column("external_mcp_servers", sa.Column("oauth_client_id", sa.Text(), nullable=False, server_default=""))
    op.add_column("external_mcp_servers", sa.Column("encrypted_oauth_client_secret", sa.Text(), nullable=False, server_default=""))
    op.add_column("external_mcp_servers", sa.Column("oauth_scopes", sa.Text(), nullable=False, server_default=""))
    op.add_column("external_mcp_servers", sa.Column("oauth_resource", sa.Text(), nullable=False, server_default=""))
    op.add_column("external_mcp_servers", sa.Column("encrypted_oauth_access_token", sa.Text(), nullable=False, server_default=""))
    op.add_column("external_mcp_servers", sa.Column("encrypted_oauth_refresh_token", sa.Text(), nullable=False, server_default=""))
    op.add_column("external_mcp_servers", sa.Column("oauth_token_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("external_mcp_servers", sa.Column("oauth_connected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("external_mcp_servers", sa.Column("oauth_last_error", sa.Text(), nullable=False, server_default=""))
    op.add_column("external_mcp_servers", sa.Column("oauth_state", sa.Text(), nullable=False, server_default=""))
    op.add_column("external_mcp_servers", sa.Column("encrypted_oauth_code_verifier", sa.Text(), nullable=False, server_default=""))
    op.add_column("external_mcp_servers", sa.Column("oauth_state_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_external_mcp_servers_oauth_state", "external_mcp_servers", ["oauth_state"])

    for column_name in (
        "oauth_authorization_url",
        "oauth_token_url",
        "oauth_client_id",
        "encrypted_oauth_client_secret",
        "oauth_scopes",
        "oauth_resource",
        "encrypted_oauth_access_token",
        "encrypted_oauth_refresh_token",
        "oauth_last_error",
        "oauth_state",
        "encrypted_oauth_code_verifier",
    ):
        op.alter_column("external_mcp_servers", column_name, server_default=None)


def downgrade() -> None:
    op.drop_index("ix_external_mcp_servers_oauth_state", table_name="external_mcp_servers")
    op.drop_column("external_mcp_servers", "oauth_state_expires_at")
    op.drop_column("external_mcp_servers", "encrypted_oauth_code_verifier")
    op.drop_column("external_mcp_servers", "oauth_state")
    op.drop_column("external_mcp_servers", "oauth_last_error")
    op.drop_column("external_mcp_servers", "oauth_connected_at")
    op.drop_column("external_mcp_servers", "oauth_token_expires_at")
    op.drop_column("external_mcp_servers", "encrypted_oauth_refresh_token")
    op.drop_column("external_mcp_servers", "encrypted_oauth_access_token")
    op.drop_column("external_mcp_servers", "oauth_resource")
    op.drop_column("external_mcp_servers", "oauth_scopes")
    op.drop_column("external_mcp_servers", "encrypted_oauth_client_secret")
    op.drop_column("external_mcp_servers", "oauth_client_id")
    op.drop_column("external_mcp_servers", "oauth_token_url")
    op.drop_column("external_mcp_servers", "oauth_authorization_url")
