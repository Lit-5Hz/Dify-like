from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.credential_crypto import decrypt_secret, encrypt_secret, mask_secret
from app.db.models import App, ModelCredential
from app.schemas import ModelCredentialCreate


def normalize_provider(provider: str) -> str:
    return str(provider or "").strip().lower()


def list_model_credentials(db: Session, owner_user_id: str) -> list[ModelCredential]:
    return list(
        db.scalars(
            select(ModelCredential)
            .where(ModelCredential.owner_user_id == owner_user_id)
            .order_by(ModelCredential.created_at.desc())
        )
    )


def get_model_credential(db: Session, credential_id: str, owner_user_id: str) -> ModelCredential | None:
    if not credential_id:
        return None
    credential = db.get(ModelCredential, credential_id)
    if not credential or credential.owner_user_id != owner_user_id:
        return None
    return credential


def create_model_credential(db: Session, payload: ModelCredentialCreate, owner_user_id: str) -> ModelCredential:
    provider = normalize_provider(payload.provider)
    api_key = payload.api_key.strip()
    name = payload.name.strip()
    if not provider:
        raise ValueError("Provider is required.")
    if not api_key:
        raise ValueError("API key is required.")
    if not name:
        name = provider

    credential = ModelCredential(
        owner_user_id=owner_user_id,
        provider=provider,
        name=name,
        encrypted_api_key=encrypt_secret(api_key),
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)
    return credential


def delete_model_credential(db: Session, credential: ModelCredential) -> None:
    if is_model_credential_in_use(db, credential.id):
        raise ValueError("This model credential is still referenced by one or more apps or agent nodes.")
    db.delete(credential)
    db.commit()


def resolve_model_api_key(db: Session, credential_id: str | None, owner_user_id: str) -> str:
    if not credential_id:
        return ""
    credential = get_model_credential(db, credential_id, owner_user_id)
    if not credential:
        raise ValueError(f"Model credential not found: {credential_id}")
    return decrypt_secret(credential.encrypted_api_key)


def to_model_credential_out(credential: ModelCredential) -> dict[str, Any]:
    api_key = decrypt_secret(credential.encrypted_api_key)
    return {
        "id": credential.id,
        "provider": credential.provider,
        "name": credential.name,
        "masked_api_key": mask_secret(api_key),
        "created_at": credential.created_at,
        "updated_at": credential.updated_at,
    }


def is_model_credential_in_use(db: Session, credential_id: str) -> bool:
    for app in db.scalars(select(App)).all():
        if getattr(app, "model_credential_id", "") == credential_id:
            return True
        workflow_spec = getattr(app, "workflow_spec", {}) or {}
        if _workflow_uses_credential(workflow_spec, credential_id):
            return True
    return False


def _workflow_uses_credential(workflow_spec: Any, credential_id: str) -> bool:
    if not isinstance(workflow_spec, dict):
        return False
    nodes = workflow_spec.get("nodes", [])
    if not isinstance(nodes, list):
        return False
    for node in nodes:
        if not isinstance(node, dict):
            continue
        model = node.get("model")
        if not isinstance(model, dict):
            continue
        if str(model.get("credential_id") or "").strip() == credential_id:
            return True
    return False
