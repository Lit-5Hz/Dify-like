from fastapi import APIRouter

from app.schemas import ToolOut
from app.tools.registry import list_tools

router = APIRouter(tags=["tools"])


@router.get("/tools", response_model=list[ToolOut])
def list_builtin_tools():
    return list_tools()
