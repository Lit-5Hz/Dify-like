from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def uuid_pk() -> Mapped[str]:
    return mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))


def now_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = uuid_pk()
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    auth_token: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, default=None)
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class App(Base):
    __tablename__ = "apps"

    id: Mapped[str] = uuid_pk()
    owner_user_id: Mapped[str] = mapped_column(String(120), default="anonymous")
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    model_provider: Mapped[str] = mapped_column(String(80), default="mock")
    model_name: Mapped[str] = mapped_column(String(120), default="mock-react")
    model_credential_id: Mapped[str] = mapped_column(String(120), default="")
    model_base_url: Mapped[str] = mapped_column(Text, default="")
    temperature: Mapped[int] = mapped_column(Integer, default=70)
    top_p: Mapped[int] = mapped_column(Integer, default=100)
    max_tokens: Mapped[int] = mapped_column(Integer, default=1024)
    workflow_spec: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tools: Mapped[list["AppTool"]] = relationship(back_populates="app", cascade="all, delete-orphan")


class ModelCredential(Base):
    __tablename__ = "model_credentials"

    id: Mapped[str] = uuid_pk()
    owner_user_id: Mapped[str] = mapped_column(String(120), default="anonymous")
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = uuid_pk()
    owner_user_id: Mapped[str] = mapped_column(String(120), default="anonymous", index=True)
    scope: Mapped[str] = mapped_column(String(32), default="creator", index=True)
    app_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    conversation_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    embedding_provider: Mapped[str] = mapped_column(String(80), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(120), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_credential_id: Mapped[str] = mapped_column(String(120), default="")
    embedding_base_url: Mapped[str] = mapped_column(Text, default="")
    qdrant_collection: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    locked: Mapped[bool] = mapped_column(default=False)
    chunk_size: Mapped[int] = mapped_column(Integer, default=512)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=64)
    chunk_strategy: Mapped[str] = mapped_column(String(32), default="auto")
    enable_parent_child: Mapped[bool] = mapped_column(default=False)
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    documents: Mapped[list["KnowledgeDocument"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = uuid_pk()
    knowledge_base_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("knowledge_bases.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    error: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = uuid_pk()
    knowledge_base_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("knowledge_bases.id", ondelete="CASCADE"))
    document_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("knowledge_documents.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    qdrant_point_id: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = now_col()

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="chunks")
    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class AppTool(Base):
    __tablename__ = "app_tools"

    id: Mapped[str] = uuid_pk()
    app_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("apps.id", ondelete="CASCADE"))
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    app: Mapped[App] = relationship(back_populates="tools")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = uuid_pk()
    app_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("apps.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(32), default="playground")
    user_id: Mapped[str] = mapped_column(String(120), default="anonymous")
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = uuid_pk()
    conversation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = now_col()


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = uuid_pk()
    app_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("apps.id", ondelete="CASCADE"))
    conversation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("conversations.id", ondelete="CASCADE"))
    input_message_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    output_message_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = now_col()


class RunStep(Base):
    __tablename__ = "run_steps"

    id: Mapped[str] = uuid_pk()
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("runs.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    input_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    output_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = now_col()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
