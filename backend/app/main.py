"""FastAPI entry point. Run with:  uvicorn app.main:app --reload"""

import logging
import os

import anthropic
from fastapi import FastAPI

from app.agent.agent import Agent
from app.agent.context import ConversationStore
from app.api.routes.chat import router as chat_router
from app.config import Settings, load_settings
from app.security.audit import AuditLog
from app.security.permissions import PolicyEngine
from app.tools.registry import build_default_registry


logger = logging.getLogger("jarvis")


def create_app(settings: Settings | None = None, client: anthropic.Anthropic | None = None) -> FastAPI:
    settings = settings or load_settings()
    if client is None and not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        logger.warning("ANTHROPIC_API_KEY is not set - add it to backend/.env or chat requests will fail.")
    app = FastAPI(title="JARVIS", version="0.1.0")
    app.state.store = ConversationStore()
    app.state.agent = Agent(
        # Credentials come from ANTHROPIC_API_KEY (or another SDK-supported source).
        client=client or anthropic.Anthropic(),
        registry=build_default_registry(settings),
        policy=PolicyEngine(settings.auto_approve_up_to),
        audit=AuditLog(settings.audit_log),
        settings=settings,
    )
    app.include_router(chat_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "model": settings.model}

    return app


app = create_app()
