from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.services.retrieval_defaults import DEFAULT_QUERY_LLM_TEMPERATURE, DEFAULT_RETRIEVAL_TOP_K


DEFAULT_RETRIEVAL_NODE = {
    "id": "retrieval",
    "type": "retrieval",
    "enabled": True,
    "knowledge_base_ids": [],
    "retrieval_top_k": DEFAULT_RETRIEVAL_TOP_K,
    "rerank_enabled": False,
    "query_enhancement_enabled": False,
    "query_enhancement_strategy": "rewrite",
    "query_llm_provider": "",
    "query_llm_model": "",
    "query_llm_credential_id": "",
    "query_llm_base_url": "",
    "query_llm_temperature": DEFAULT_QUERY_LLM_TEMPERATURE,
}

DEFAULT_WORKFLOW_SPEC = {
    "nodes": [
        {"id": "start", "type": "start"},
        deepcopy(DEFAULT_RETRIEVAL_NODE),
        {
            "id": "agent",
            "type": "react_agent",
            "tools": [
                {
                    "type": "builtin",
                    "name": "query_order",
                    "enabled": True,
                    "config": {},
                }
            ],
            "model": {},
        },
        {"id": "end", "type": "end"},
    ],
    "edges": [["start", "retrieval"], ["retrieval", "agent"], ["agent", "end"]],
}


class AppCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    system_prompt: str = "你是一个专业、耐心、简洁的智能体。"
    model_provider: str = "openai_compatible"
    model_name: str = ""
    model_credential_id: str = ""
    model_base_url: str = ""
    temperature: int = 70
    top_p: int = 100
    max_tokens: int = 1024


class AppUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    model_credential_id: str | None = None
    model_base_url: str | None = None
    temperature: int | None = None
    top_p: int | None = None
    max_tokens: int | None = None


class AppOut(BaseModel):
    id: str
    owner_user_id: str
    name: str
    description: str
    system_prompt: str
    model_provider: str
    model_name: str
    model_credential_id: str
    model_base_url: str
    temperature: int
    top_p: int
    max_tokens: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowCreate(BaseModel):
    name: str = Field(default="Default workflow", min_length=1, max_length=120)
    description: str = ""
    draft_spec: dict[str, Any] = Field(default_factory=lambda: deepcopy(DEFAULT_WORKFLOW_SPEC))


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    draft_spec: dict[str, Any] | None = None


class WorkflowOut(BaseModel):
    id: str
    app_id: str
    name: str
    description: str
    draft_spec: dict[str, Any]
    published_version_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowVersionOut(BaseModel):
    id: str
    workflow_id: str
    version_number: int
    spec_json: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkflowMcpServerUpsert(BaseModel):
    enabled: bool = True
    server_name: str = Field(min_length=1, max_length=120)
    server_slug: str = Field(min_length=1, max_length=160)
    description: str = ""


class WorkflowMcpServerOut(BaseModel):
    id: str
    workflow_id: str
    enabled: bool
    server_name: str
    server_slug: str
    description: str
    auth_type: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowMcpServerProvisionOut(WorkflowMcpServerOut):
    token: str | None = None


class ExternalMcpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    transport_type: str = "streamable_http"
    server_url: str = Field(min_length=1)
    auth_type: str = "none"
    auth_secret: str = ""
    custom_headers: dict[str, str] | None = None
    oauth_authorization_url: str = ""
    oauth_token_url: str = ""
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    oauth_scopes: str = ""
    oauth_resource: str | None = None


class ExternalMcpServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    transport_type: str | None = None
    server_url: str | None = None
    auth_type: str | None = None
    auth_secret: str | None = None
    custom_headers: dict[str, str] | None = None
    oauth_authorization_url: str | None = None
    oauth_token_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_scopes: str | None = None
    oauth_resource: str | None = None


class ExternalMcpServerOut(BaseModel):
    id: str
    owner_user_id: str
    name: str
    description: str
    transport_type: str
    server_url: str
    auth_type: str
    has_auth_secret: bool
    has_custom_headers: bool
    custom_header_names: list[str]
    has_mcp_session: bool
    oauth_authorization_url: str
    oauth_token_url: str
    oauth_client_id: str
    oauth_scopes: str
    oauth_resource: str
    oauth_connected: bool
    oauth_token_expires_at: datetime | None
    oauth_last_error: str
    status: str
    last_sync_at: datetime | None
    last_sync_error: str
    tool_manifest_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExternalMcpToolOut(BaseModel):
    server_id: str
    name: str
    description: str
    input_schema: dict[str, Any]


class ExternalMcpOAuthConnectOut(BaseModel):
    authorization_url: str


class UserCreate(BaseModel):
    email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: str
    email: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    token: str
    user: UserOut


class ToolOut(BaseModel):
    name: str
    label: str
    description: str


class ModelCredentialCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    api_key: str = Field(min_length=1)


class ModelCredentialOut(BaseModel):
    id: str
    provider: str
    name: str
    masked_api_key: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChatRequest(BaseModel):
    query: str
    conversation_id: str | None = None
    stream: bool = True


class ChatResponse(BaseModel):
    conversation_id: str
    run_id: str
    answer: str
    tool_calls: list[dict[str, Any]]
    retrieved_chunks: list[dict[str, Any]]


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None


class KnowledgeBaseOut(BaseModel):
    id: str
    owner_user_id: str
    scope: str
    app_id: str
    conversation_id: str
    name: str
    description: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    embedding_credential_id: str
    embedding_base_url: str
    qdrant_collection: str
    locked: bool
    chunk_size: int
    chunk_overlap: int
    chunk_strategy: str
    enable_parent_child: bool
    config_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeDocumentOut(BaseModel):
    id: str
    knowledge_base_id: str
    filename: str
    mime_type: str
    status: str
    error: str
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id: str
    app_id: str
    workflow_id: str
    workflow_version_id: str
    conversation_id: str
    status: str
    latency_ms: int
    error: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RunStepOut(BaseModel):
    id: str
    run_id: str
    type: str
    name: str
    input_json: dict[str, Any]
    output_json: dict[str, Any]
    latency_ms: int
    error: str
    started_at: datetime
    ended_at: datetime | None

    model_config = {"from_attributes": True}


class PlatformSkillOut(BaseModel):
    id: str
    owner_user_id: str
    name: str
    description: str
    version: str
    status: str
    visibility: str = "private"
    publish_status: str = "draft"
    source_skill_id: str = ""
    source_app_id: str
    source_workflow_id: str
    source_run_id: str
    published_at: datetime | None = None
    revoked_at: datetime | None = None
    usage_count: int = 0
    last_used_at: datetime | None = None
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkillValidateRequest(BaseModel):
    skill_id: str | None = None
    files: dict[str, str] = Field(default_factory=dict)


class SkillValidationOut(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)


class SkillSynthesizeRequest(BaseModel):
    run_id: str = Field(min_length=1)
    feedback: str = ""
    skill_name: str | None = Field(default=None, min_length=1, max_length=120)
    skill_id: str | None = None


class AssistantWorkflowRecommendation(BaseModel):
    workflow_id: str
    app_id: str
    app_name: str
    workflow_name: str
    description: str
    version_id: str


class AssistantLoadedSkill(BaseModel):
    skill_id: str
    name: str
    version: str
    visibility: str = "private"
    loaded_files: list[str]
    load_stages: list[str] = Field(default_factory=list)
    summary: str
    score: float = 0.0
    match_summary: str = ""
    deferred_references: list[str] = Field(default_factory=list)
    loaded_references: list[str] = Field(default_factory=list)


class PlatformAssistantChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    skill_ids: list[str] = Field(default_factory=list)


class PlatformAssistantMessageOut(BaseModel):
    role: str
    content: str


class PlatformAssistantChatResponse(BaseModel):
    conversation_id: str
    answer: str
    messages: list[PlatformAssistantMessageOut] = Field(default_factory=list)
    recommendations: list[AssistantWorkflowRecommendation] = Field(default_factory=list)
    loaded_skills: list[AssistantLoadedSkill] = Field(default_factory=list)
    load_stages: list[dict[str, Any]] = Field(default_factory=list)
    deferred_references: list[dict[str, Any]] = Field(default_factory=list)
    loaded_references: list[dict[str, Any]] = Field(default_factory=list)
    suggested_app: dict[str, Any] = Field(default_factory=dict)
    suggested_workflow: dict[str, Any] = Field(default_factory=dict)
    draft_explanation: dict[str, Any] = Field(default_factory=dict)
    created_app: AppOut | None = None
    created_workflow: WorkflowOut | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    model_status: str = "fallback"
    model_message: str = ""


class PlatformAssistantApplyRequest(BaseModel):
    app_name: str = Field(min_length=1, max_length=120)
    app_description: str = ""
    workflow_name: str = Field(default="Default workflow", min_length=1, max_length=120)
    workflow_description: str = ""
    draft_spec: dict[str, Any]


class PlatformAssistantApplyResponse(BaseModel):
    app: AppOut
    workflow: WorkflowOut
