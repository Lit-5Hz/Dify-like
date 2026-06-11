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

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
