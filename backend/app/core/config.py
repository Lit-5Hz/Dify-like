import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/dify_like"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    document_inline_ingest: bool = True

    knowledge_embedding_provider: str = ""
    knowledge_embedding_model: str = ""
    knowledge_embedding_dimension: int = 0
    knowledge_embedding_api_key: str = ""
    knowledge_embedding_base_url: str = ""

    retrieval_jina_api_key: str = ""
    retrieval_jina_model: str = "jina-reranker-v2"
    retrieval_jina_base_url: str = "https://api.jina.ai/v1"

    document_unstructured_api_url: str = ""
    document_unstructured_api_key: str = ""
    document_mineru_api_url: str = ""
    document_mineru_api_key: str = ""

    storage_dir: Path = Path("./storage")
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    public_api_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:5173"
    mcp_tool_timeout_seconds: float = 120.0

    platform_assistant_api_base_url: str = ""
    platform_assistant_api_key: str = ""
    platform_assistant_model: str = ""
    platform_assistant_temperature: float = 0.2
    platform_assistant_timeout_seconds: int = 30

    agentscope_tracing_url: str = "http://localhost:4318/v1/traces"
    agentscope_tracing_service_name: str = "dify-like-agent-runtime"

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings


def initialize_agentscope_tracing(settings: Settings) -> None:
    tracing_url = settings.agentscope_tracing_url.strip()
    if not tracing_url:
        return

    os.environ.setdefault("OTEL_SERVICE_NAME", settings.agentscope_tracing_service_name)
    import agentscope

    agentscope.init(
        project="dify-like",
        name="backend",
        tracing_url=tracing_url,
    )


def activate_agentscope_tracing_context(settings: Settings) -> None:
    if not settings.agentscope_tracing_url.strip():
        return

    # AgentScope 1.0.19 stores these values in ContextVar instances, so the
    # FastAPI request context must be activated separately from startup.
    from agentscope import _config

    _config.project = "dify-like"
    _config.name = "backend"
    _config.trace_enabled = True
