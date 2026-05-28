from fastapi import APIRouter

from app.services.retrieval_service import get_capabilities

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.get("/capabilities")
def capabilities():
    return get_capabilities()
