"""FastAPI entry point. Run with:  uvicorn app.main:app --reload"""

import json
import logging
import os
import urllib.request

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
    client = client or make_client(settings)
    engine = engine or make_engine(settings.database_url)
    sessions = make_session_factory(engine)

    app = FastAPI(title="JARVIS", version="0.2.0")
    app.state.store = ConversationStore(sessions)
    app.state.agent = Agent(
        client=client,
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
        return {"status": "ok", "provider": settings.provider, "model": settings.model,
                "model_status": model_status(settings), "database": database}

    return app


def make_client(settings: Settings) -> anthropic.Anthropic:
    if settings.provider == "ollama":
        # Ollama speaks the Messages API at its root URL; it ignores the key but the SDK requires one.
        return anthropic.Anthropic(base_url=settings.ollama_url, api_key="ollama")
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        logger.warning("ANTHROPIC_API_KEY is not set - add it to backend/.env or chat requests will fail.")
    # Credentials come from ANTHROPIC_API_KEY (or another SDK-supported source).
    return anthropic.Anthropic()


def model_status(settings: Settings) -> str:
    if settings.provider != "ollama":
        return "not checked"
    try:
        with urllib.request.urlopen(f"{settings.ollama_url}/api/tags", timeout=2) as response:
            names = {m["name"] for m in json.loads(response.read())["models"]}
    except OSError:
        return f"Ollama not reachable at {settings.ollama_url} - is it running?"
    if settings.model in names or f"{settings.model}:latest" in names:
        return "ok"
    return f"model '{settings.model}' not installed - see README (Ollama setup)"


app = create_app()
