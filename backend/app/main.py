"""FastAPI entry point. Run with:  uvicorn app.main:app --reload"""

import logging
import os

import anthropic
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.agent.agent import Agent
from app.api.routes.chat import router as chat_router
from app.config import Settings, load_settings
from app.db.session import make_engine, make_session_factory
from app.memory.short_term import ConversationStore
from app.security.audit import AuditLog
from app.security.permissions import PolicyEngine
from app.tools.registry import build_default_registry

logger = logging.getLogger("jarvis")


def create_app(
    settings: Settings | None = None,
    client: anthropic.Anthropic | None = None,
    engine: Engine | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    if client is None and not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        logger.warning("ANTHROPIC_API_KEY is not set - add it to backend/.env or chat requests will fail.")

    engine = engine or make_engine(settings.database_url)
    sessions = make_session_factory(engine)

    app = FastAPI(title="JARVIS", version="0.2.0")
    app.state.store = ConversationStore(sessions)
    app.state.agent = Agent(
        # Credentials come from ANTHROPIC_API_KEY (or another SDK-supported source).
        client=client or anthropic.Anthropic(),
        store=app.state.store,
        registry=build_default_registry(settings),
        policy=PolicyEngine(settings.auto_approve_up_to),
        audit=AuditLog(sessions),
        settings=settings,
    )
    app.include_router(chat_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            database = "ok"
        except Exception as e:
            database = f"unavailable ({type(e).__name__})"
        return {"status": "ok", "model": settings.model, "database": database}

    return app


app = create_app()
