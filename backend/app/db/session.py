from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_app_columns()
    _ensure_model_credential_columns()
    _ensure_knowledge_base_columns()


def _ensure_app_columns() -> None:
    inspector = inspect(engine)
    if "apps" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("apps")}
    statements = []
    if "owner_user_id" not in existing_columns:
        statements.append("ALTER TABLE apps ADD COLUMN owner_user_id VARCHAR(120) NOT NULL DEFAULT 'anonymous'")
    if "status" not in existing_columns:
        statements.append("ALTER TABLE apps ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'draft'")
    if "model_credential_id" not in existing_columns:
        statements.append("ALTER TABLE apps ADD COLUMN model_credential_id VARCHAR(120) NOT NULL DEFAULT ''")
    if "model_base_url" not in existing_columns:
        statements.append("ALTER TABLE apps ADD COLUMN model_base_url TEXT NOT NULL DEFAULT ''")
    _execute_statements(statements)


def _ensure_model_credential_columns() -> None:
    inspector = inspect(engine)
    if "model_credentials" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("model_credentials")}
    statements = []
    if "owner_user_id" not in existing_columns:
        statements.append("ALTER TABLE model_credentials ADD COLUMN owner_user_id VARCHAR(120) NOT NULL DEFAULT 'anonymous'")
    _execute_statements(statements)


def _ensure_knowledge_base_columns() -> None:
    inspector = inspect(engine)
    if "knowledge_bases" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("knowledge_bases")}
    statements = []
    if "scope" not in existing_columns:
        statements.append("ALTER TABLE knowledge_bases ADD COLUMN scope VARCHAR(32) NOT NULL DEFAULT 'creator'")
    if "app_id" not in existing_columns:
        statements.append("ALTER TABLE knowledge_bases ADD COLUMN app_id VARCHAR(120) NOT NULL DEFAULT ''")
    if "conversation_id" not in existing_columns:
        statements.append("ALTER TABLE knowledge_bases ADD COLUMN conversation_id VARCHAR(120) NOT NULL DEFAULT ''")
    _execute_statements(statements)


def _execute_statements(statements: list[str]) -> None:
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
