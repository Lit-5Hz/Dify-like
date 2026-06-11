from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.credential_crypto import decrypt_secret, encrypt_secret
from app.db.models import ExternalMcpServer


OAUTH_STATE_TTL_MINUTES = 10
OAUTH_REFRESH_SKEW_SECONDS = 60
OAUTH_HTTP_TIMEOUT = 15.0


@dataclass(frozen=True)
class ExternalMcpAuthContext:
    request_auth_type: str
    auth_secret: str


def build_oauth_redirect_uri(settings: Settings | None = None) -> str:
    active_settings = settings or get_settings()
    return f"{active_settings.public_api_base_url.rstrip('/')}/api/mcp/oauth/callback"


def start_external_mcp_oauth_flow(
    db: Session,
    server: ExternalMcpServer,
    settings: Settings | None = None,
) -> str:
    _validate_oauth_connect_config(server)
    active_settings = settings or get_settings()
    redirect_uri = build_oauth_redirect_uri(active_settings)

    state = secrets.token_urlsafe(32)
    code_verifier = _build_code_verifier()
    code_challenge = _build_code_challenge(code_verifier)

    server.oauth_state = state
    server.encrypted_oauth_code_verifier = encrypt_secret(code_verifier)
    server.oauth_state_expires_at = datetime.now(timezone.utc) + timedelta(minutes=OAUTH_STATE_TTL_MINUTES)
    server.oauth_last_error = ""
    db.commit()
    db.refresh(server)

    params = {
        "response_type": "code",
        "client_id": server.oauth_client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    scopes = str(server.oauth_scopes or "").strip()
    if scopes:
        params["scope"] = scopes
    resource = str(server.oauth_resource or "").strip()
    if resource:
        params["resource"] = resource
    separator = "&" if "?" in server.oauth_authorization_url else "?"
    return f"{server.oauth_authorization_url}{separator}{urlencode(params)}"


async def complete_external_mcp_oauth_callback(
    db: Session,
    state: str,
    code: str,
    settings: Settings | None = None,
) -> ExternalMcpServer:
    server = _get_server_by_oauth_state(db, state)
    if not server:
        raise ValueError("OAuth state was not found or has already been used.")
    if _oauth_state_expired(server):
        _clear_oauth_flow_state(server)
        server.oauth_last_error = "OAuth state expired."
        db.commit()
        raise ValueError("OAuth state expired. Start OAuth connection again.")
    if not code:
        raise ValueError("OAuth callback requires an authorization code.")

    verifier = _decrypt_optional(server.encrypted_oauth_code_verifier)
    if not verifier:
        raise ValueError("OAuth code verifier is missing. Start OAuth connection again.")

    active_settings = settings or get_settings()
    token_payload = await _request_oauth_token(
        server,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": build_oauth_redirect_uri(active_settings),
            "client_id": server.oauth_client_id,
            "code_verifier": verifier,
        },
    )
    _store_oauth_token_payload(server, token_payload)
    _clear_oauth_flow_state(server)
    server.oauth_connected_at = datetime.now(timezone.utc)
    server.oauth_last_error = ""
    server.status = "pending_sync"
    server.last_sync_at = None
    server.last_sync_error = ""
    server.tool_manifest_json = {"tools": []}
    server.mcp_session_id = ""
    db.commit()
    db.refresh(server)
    return server


def disconnect_external_mcp_oauth(db: Session, server: ExternalMcpServer) -> ExternalMcpServer:
    _clear_oauth_tokens(server)
    _clear_oauth_flow_state(server)
    server.oauth_last_error = ""
    server.status = "pending_sync"
    server.last_sync_at = None
    server.last_sync_error = ""
    server.tool_manifest_json = {"tools": []}
    server.mcp_session_id = ""
    db.commit()
    db.refresh(server)
    return server


async def resolve_external_mcp_auth_context(
    db: Session,
    server: ExternalMcpServer,
    *,
    force_oauth_refresh: bool = False,
) -> ExternalMcpAuthContext:
    auth_type = str(server.auth_type or "none").strip().lower()
    if auth_type == "none":
        return ExternalMcpAuthContext(request_auth_type="none", auth_secret="")
    if auth_type == "bearer":
        secret = _decrypt_optional(server.encrypted_auth_secret)
        if not secret:
            raise ValueError("Missing bearer token for external MCP server.")
        return ExternalMcpAuthContext(request_auth_type="bearer", auth_secret=secret)
    if auth_type == "oauth2":
        token = await resolve_oauth_access_token(db, server, force_refresh=force_oauth_refresh)
        return ExternalMcpAuthContext(request_auth_type="bearer", auth_secret=token)
    raise ValueError(f"Unsupported MCP auth type: {server.auth_type}")


async def resolve_oauth_access_token(
    db: Session,
    server: ExternalMcpServer,
    *,
    force_refresh: bool = False,
) -> str:
    if str(server.auth_type or "").strip().lower() != "oauth2":
        raise ValueError("External MCP server is not configured for OAuth2.")

    access_token = _decrypt_optional(server.encrypted_oauth_access_token)
    if access_token and not force_refresh and not _token_needs_refresh(server):
        return access_token

    refresh_token = _decrypt_optional(server.encrypted_oauth_refresh_token)
    if not refresh_token:
        if access_token and not _token_expired(server):
            return access_token
        raise ValueError("OAuth is not connected. Connect OAuth before syncing or calling tools.")

    try:
        token_payload = await _request_oauth_token(
            server,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": server.oauth_client_id,
            },
        )
    except Exception as exc:
        server.oauth_last_error = str(exc)
        db.commit()
        raise ValueError(f"OAuth token refresh failed: {exc}") from exc

    _store_oauth_token_payload(server, token_payload, fallback_refresh_token=refresh_token)
    server.oauth_last_error = ""
    db.commit()
    db.refresh(server)
    return _decrypt_optional(server.encrypted_oauth_access_token)


def external_mcp_oauth_callback_success_url(server: ExternalMcpServer, settings: Settings | None = None) -> str:
    active_settings = settings or get_settings()
    return f"{active_settings.frontend_base_url.rstrip('/')}?mcp_oauth=connected&server_id={server.id}"


def external_mcp_oauth_callback_error_url(message: str, settings: Settings | None = None) -> str:
    active_settings = settings or get_settings()
    return f"{active_settings.frontend_base_url.rstrip('/')}?mcp_oauth=error&message={quote(message)}"


def _validate_oauth_connect_config(server: ExternalMcpServer) -> None:
    if str(server.auth_type or "").strip().lower() != "oauth2":
        raise ValueError("External MCP server auth_type must be oauth2.")
    if not str(server.oauth_authorization_url or "").strip():
        raise ValueError("OAuth authorization URL is required.")
    if not str(server.oauth_token_url or "").strip():
        raise ValueError("OAuth token URL is required.")
    if not str(server.oauth_client_id or "").strip():
        raise ValueError("OAuth client ID is required.")


def _get_server_by_oauth_state(db: Session, state: str) -> ExternalMcpServer | None:
    cleaned = str(state or "").strip()
    if not cleaned:
        return None
    return db.scalar(select(ExternalMcpServer).where(ExternalMcpServer.oauth_state == cleaned))


def _oauth_state_expired(server: ExternalMcpServer) -> bool:
    expires_at = server.oauth_state_expires_at
    if not expires_at:
        return True
    return _ensure_aware(expires_at) <= datetime.now(timezone.utc)


def _token_needs_refresh(server: ExternalMcpServer) -> bool:
    expires_at = server.oauth_token_expires_at
    if not expires_at:
        return False
    return _ensure_aware(expires_at) <= datetime.now(timezone.utc) + timedelta(seconds=OAUTH_REFRESH_SKEW_SECONDS)


def _token_expired(server: ExternalMcpServer) -> bool:
    expires_at = server.oauth_token_expires_at
    return bool(expires_at and _ensure_aware(expires_at) <= datetime.now(timezone.utc))


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _build_code_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def _build_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def _request_oauth_token(server: ExternalMcpServer, data: dict[str, str]) -> dict[str, Any]:
    token_url = str(server.oauth_token_url or "").strip()
    if not token_url:
        raise ValueError("OAuth token URL is required.")

    client_secret = _decrypt_optional(server.encrypted_oauth_client_secret)
    if client_secret:
        data = {**data, "client_secret": client_secret}
    resource = str(server.oauth_resource or "").strip()
    if resource:
        data = {**data, "resource": resource}

    async with httpx.AsyncClient(timeout=OAUTH_HTTP_TIMEOUT) as client:
        response = await client.post(token_url, data=data, headers={"Accept": "application/json"})
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"OAuth token endpoint returned HTTP {response.status_code}: {response.text}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("OAuth token endpoint did not return JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("OAuth token endpoint returned an invalid response.")
    if payload.get("error"):
        description = payload.get("error_description") or payload.get("error")
        raise ValueError(f"OAuth token endpoint returned an error: {description}")
    if not str(payload.get("access_token") or "").strip():
        raise ValueError("OAuth token endpoint response is missing access_token.")
    return payload


def _store_oauth_token_payload(
    server: ExternalMcpServer,
    payload: dict[str, Any],
    *,
    fallback_refresh_token: str = "",
) -> None:
    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or "").strip() or fallback_refresh_token
    expires_in = payload.get("expires_in")

    server.encrypted_oauth_access_token = encrypt_secret(access_token)
    server.encrypted_oauth_refresh_token = encrypt_secret(refresh_token) if refresh_token else ""
    server.oauth_token_expires_at = _build_expires_at(expires_in)


def _build_expires_at(expires_in: Any) -> datetime | None:
    if expires_in is None or expires_in == "":
        return None
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return datetime.now(timezone.utc)
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _clear_oauth_tokens(server: ExternalMcpServer) -> None:
    server.encrypted_oauth_access_token = ""
    server.encrypted_oauth_refresh_token = ""
    server.oauth_token_expires_at = None
    server.oauth_connected_at = None


def _clear_oauth_flow_state(server: ExternalMcpServer) -> None:
    server.oauth_state = ""
    server.encrypted_oauth_code_verifier = ""
    server.oauth_state_expires_at = None


def _decrypt_optional(value: str | None) -> str:
    encrypted = str(value or "").strip()
    if not encrypted:
        return ""
    return decrypt_secret(encrypted)
