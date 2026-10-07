"""FastAPI entry point. Run with:  uvicorn app.main:app --reload"""

import json
import logging
import os
import threading
import urllib.request
from contextlib import asynccontextmanager

import anthropic
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.agent.agent import Agent
from app.api.routes.chat import router as chat_router
from app.api.routes.memory import router as memory_router
from app.config import Settings, load_settings
from app.db.session import make_engine, make_session_factory
from app.memory.embeddings import Embedder
from app.memory.long_term import FactStore
from app.memory.retrieval import Retriever
from app.memory.semantic import DocumentStore
from app.memory.short_term import ConversationStore
from app.security.audit import AuditLog
from app.security.permissions import PolicyEngine
from app.tools.browser import BrowserSession
from app.tools.desktop import DesktopSession
from app.tools.filesystem import Sandbox
from app.tools.registry import build_default_registry
from app.tools.screen import ScreenReader

logger = logging.getLogger("jarvis")


def create_app(
    settings: Settings | None = None,
    client: anthropic.Anthropic | None = None,
    engine: Engine | None = None,
    embedder: Embedder | None = None,
    browser: BrowserSession | None = None,
    desktop: DesktopSession | None = None,
    screen: ScreenReader | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    warm_up_models = client is None and settings.provider == "ollama"  # skipped when tests inject a client
    client = client or make_client(settings)
    engine = engine or make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    # Embeddings always come from local Ollama, whichever provider runs the chat model.
    embedder = embedder or Embedder(settings.ollama_url, settings.embedding_model)
    retriever = Retriever(
        embedder,
        FactStore(sessions, embedder),
        DocumentStore(sessions, embedder),
        min_score=settings.memory_min_similarity,
    )
    # Started lazily on the first browser tool call; closed when the server stops.
    browser = browser or BrowserSession(settings.browser_channel, settings.browser_headless)
    if desktop is None and settings.desktop_control and DesktopSession.available():
        desktop = DesktopSession()
    screen = screen or ScreenReader(settings.ollama_url, settings.vision_model)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if warm_up_models:
            threading.Thread(target=warm_up, args=(app.state.agent,), daemon=True).start()
        yield
        browser.close()
        if desktop is not None:
            desktop.close()

    app = FastAPI(title="JARVIS", version="0.4.0", lifespan=lifespan)
    app.state.store = ConversationStore(sessions)
    app.state.retriever = retriever
    app.state.sandbox = Sandbox(settings.allowed_roots)
    app.state.agent = Agent(
        client=client,
        store=app.state.store,
        registry=build_default_registry(settings, retriever, browser, desktop, screen),
        policy=PolicyEngine(settings.auto_approve_up_to),
        audit=AuditLog(sessions),
        settings=settings,
        retriever=retriever,
    )
    app.include_router(chat_router)
    app.include_router(memory_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            database = "ok"
        except Exception as e:
            database = f"unavailable ({type(e).__name__})"
        chat_model = (ollama_model_status(settings.ollama_url, settings.model)
                      if settings.provider == "ollama" else "not checked")
        return {"status": "ok", "provider": settings.provider, "model": settings.model,
                "model_status": chat_model,
                "embedding_model_status": ollama_model_status(settings.ollama_url, settings.embedding_model),
                "vision_model_status": ollama_model_status(settings.ollama_url, settings.vision_model),
                "database": database,
                "browser": "running" if browser.running else "starts on first use",
                "desktop_control": "on" if desktop is not None else "off"}

    return app


def warm_up(agent: Agent) -> None:
    """Load the local models and cache the system prompt + tools in Ollama, so the first real
    message doesn't pay a cold start (which can take over a minute after a long idle)."""
    try:
        agent.client.messages.create(
            model=agent.settings.model, max_tokens=1, system=agent.system_prompt,
            tools=agent.registry.definitions(), messages=[{"role": "user", "content": "hi"}],
        )
        if agent.retriever is not None:
            agent.retriever.embedder.embed_query("warm up")
        logger.info("Local models warmed up.")
    except Exception as e:  # warm-up is best effort; chat reports real errors itself
        logger.warning("Model warm-up failed: %s", e)


def make_client(settings: Settings) -> anthropic.Anthropic:
    if settings.provider == "ollama":
        # Ollama speaks the Messages API at its root URL; it ignores the key but the SDK requires one.
        return anthropic.Anthropic(base_url=settings.ollama_url, api_key="ollama")
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        logger.warning("ANTHROPIC_API_KEY is not set - add it to backend/.env or chat requests will fail.")
    # Credentials come from ANTHROPIC_API_KEY (or another SDK-supported source).
    return anthropic.Anthropic()


def ollama_model_status(ollama_url: str, model: str) -> str:
    try:
        with urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=2) as response:
            names = {m["name"] for m in json.loads(response.read())["models"]}
    except OSError:
        return f"Ollama not reachable at {ollama_url} - is it running?"
    if model in names or f"{model}:latest" in names:
        return "ok"
    return f"model '{model}' not installed - see README (Ollama setup)"


app = create_app()
