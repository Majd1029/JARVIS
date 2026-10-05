"""Runtime settings, read from environment variables (and a local .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

from app.security.permissions import PermissionLevel

# backend/.env, wherever the server is launched from.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

Provider = Literal["ollama", "anthropic"]

# ollama: a free local model. anthropic: the Claude API (needs paid credits).
DEFAULT_MODELS: dict[str, str] = {"ollama": "jarvis-qwen3", "anthropic": "claude-opus-5-5"}


@dataclass(frozen=True)
class Settings:
    provider: Provider
    model: str
    ollama_url: str
    effort: str
    max_tokens: int
    max_agent_steps: int
    allowed_roots: tuple[Path, ...]
    auto_approve_up_to: PermissionLevel
    database_url: str
    embedding_model: str = "nomic-embed-text"
    memory_min_similarity: float = 0.55  # how related a memory must be to attach it to a message


def load_settings() -> Settings:
    provider = os.getenv("JARVIS_PROVIDER", "ollama").strip().lower()
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"JARVIS_PROVIDER must be one of {', '.join(DEFAULT_MODELS)}, not '{provider}'")

    roots_env = os.getenv("JARVIS_ALLOWED_ROOTS", "")
    roots = [Path(p).expanduser() for p in roots_env.split(os.pathsep) if p.strip()]
    if not roots:
        roots = [Path.home()]

    return Settings(
        provider=provider,
        model=os.getenv("JARVIS_MODEL") or DEFAULT_MODELS[provider],
        ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
        effort=os.getenv("JARVIS_EFFORT", "medium"),
        max_tokens=int(os.getenv("JARVIS_MAX_TOKENS", "16000" if provider == "anthropic" else "4096")),
        max_agent_steps=int(os.getenv("JARVIS_MAX_AGENT_STEPS", "15")),
        allowed_roots=tuple(r.resolve() for r in roots),
        auto_approve_up_to=PermissionLevel(int(os.getenv("JARVIS_AUTO_APPROVE_LEVEL", "1"))),
        database_url=os.getenv(
            "DATABASE_URL", "postgresql+psycopg://jarvis:jarvis@127.0.0.1:5433/jarvis"
        ),
        embedding_model=os.getenv("JARVIS_EMBEDDING_MODEL", "nomic-embed-text"),
        memory_min_similarity=float(os.getenv("JARVIS_MEMORY_MIN_SIMILARITY", "0.55")),
    )
