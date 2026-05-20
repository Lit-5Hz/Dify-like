from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


DEFAULT_WORKFLOW_SPEC = {
    "nodes": [
        {"id": "start", "type": "start"},
        {"id": "retrieval", "type": "retrieval", "enabled": True, "top_k": 3},
        {
            "id": "agent",
            "type": "react_agent",
            "model": {},
        },
        {"id": "end", "type": "end"},
    ],
    "edges": [["start", "retrieval"], ["retrieval", "agent"], ["agent", "end"]],
}


class AppCreate(BaseModel):
    # 这是“创建 App 请求体”的数据结构，不是数据库对象本身。
    # FastAPI 会根据它校验前端传来的 JSON，并在缺省字段时自动补上这里的默认值。
    # 目前这些默认值是 MVP 阶段的创建页预设，用来快速生成一个可运行的 demo app。
    # 等产品成熟后，默认值通常应来自前端模板、产品配置或创建向导，而不是长期硬编码在 schema 里。
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    system_prompt: str = "你是一个专业、耐心的电商客服智能体。"  # MVP 默认 prompt，后续可改成模板/配置驱动
    model_provider: str = "mock"  # MVP 默认模型提供方，后续应由用户选择或从配置读取
    model_name: str = "mock-react"  # MVP 默认模型名，后续应由用户选择或从配置读取
    model_credential_id: str = ""  # 模型凭据引用；App 只保存凭据 id，不保存密钥本身
    model_base_url: str = ""  # OpenAI-compatible / vLLM / DeepSeek 等服务的 base URL
    temperature: int = 70  # MVP 默认采样参数；运行时会把 70 这类百分比值换算成 0.7
    top_p: int = 100  # MVP 默认采样参数；运行时会把 100 换算成 1.0
    max_tokens: int = 1024  # MVP 默认输出长度限制
    workflow_spec: dict[str, Any] = Field(default_factory=lambda: deepcopy(DEFAULT_WORKFLOW_SPEC))  # MVP 默认 workflow，后续可改为模板或可视化编辑结果


class AppUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    system_prompt: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    model_credential_id: str | None = None
    model_base_url: str | None = None
    temperature: int | None = None
    top_p: int | None = None
    max_tokens: int | None = None
    workflow_spec: dict[str, Any] | None = None


class AppOut(BaseModel):
    id: str
    name: str
    description: str
    status: str
    system_prompt: str
    model_provider: str
    model_name: str
    model_credential_id: str
    model_base_url: str
    temperature: int
    top_p: int
    max_tokens: int
    workflow_spec: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


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


class AppToolUpdate(BaseModel):
    tool_names: list[str]


class AppToolOut(BaseModel):
    tool_name: str
    enabled: bool


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


class DocumentOut(BaseModel):
    id: str
    app_id: str
    filename: str
    status: str
    error: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id: str
    app_id: str
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
