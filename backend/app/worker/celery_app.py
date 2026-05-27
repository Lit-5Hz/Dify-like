from __future__ import annotations

from celery import Celery

from app.core.config import get_settings


settings = get_settings()
celery_app = Celery("dify_like", broker=settings.redis_url, backend=settings.redis_url)
app = celery_app


@celery_app.task(name="rag.process_document")
def process_knowledge_document(document_id: str) -> None:
    from app.services.rag_service import process_knowledge_document_sync

    process_knowledge_document_sync(document_id)
