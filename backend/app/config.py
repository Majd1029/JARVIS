"""Runtime settings, read from environment variables (and a local .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from app.security.permissions import PermissionLevel

load_dotenv()


@dataclass(frozen=True)
class Settings:
    model: str
    effort: str
    max_tokens: int
    max_agent_steps: int
    allowed_roots: tuple[Path, ...]
    auto_approve_up_to: PermissionLevel
    audit_log: Path


def load_settings() -> Settings:
    roots_env = os.getenv("JARVIS_ALLOWED_ROOTS", "")
    roots = [Path(p).expanduser() for p in roots_env.split(os.pathsep) if p.strip()]
    if not roots:
        roots = [Path.home()]

    return Settings(
        model=os.getenv("JARVIS_MODEL", "claude-opus-5-5"),
        effort=os.getenv("JARVIS_EFFORT", "medium"),
        max_tokens=int(os.getenv("JARVIS_MAX_TOKENS", "16000")),
        max_agent_steps=int(os.getenv("JARVIS_MAX_AGENT_STEPS", "15")),
        allowed_roots=tuple(r.resolve() for r in roots),
        auto_approve_up_to=PermissionLevel(int(os.getenv("JARVIS_AUTO_APPROVE_LEVEL", "1"))),
        audit_log=Path(os.getenv("JARVIS_AUDIT_LOG", "data/audit.jsonl")),
    )
