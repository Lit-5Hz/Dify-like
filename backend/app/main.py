from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    apps,
    auth,
    chat,
    external_mcp_servers,
    knowledge_bases,
    mcp_server,
    model_credentials,
    platform_assistant,
    retrieval,
    runs,
    skills,
    tools,
    workflows,
)
from app.core.config import get_settings, initialize_agentscope_tracing
from app.db.session import init_db


settings = get_settings()

app = FastAPI(title="Dify-like API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(apps.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(model_credentials.router, prefix="/api")
app.include_router(tools.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(platform_assistant.router, prefix="/api")
app.include_router(skills.router, prefix="/api")
app.include_router(knowledge_bases.router, prefix="/api")
app.include_router(retrieval.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")
app.include_router(mcp_server.api_router, prefix="/api")
app.include_router(external_mcp_servers.router, prefix="/api")
app.include_router(external_mcp_servers.oauth_router, prefix="/api")
app.include_router(mcp_server.public_router)


@app.on_event("startup")
def on_startup() -> None:
    initialize_agentscope_tracing(settings)
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}
