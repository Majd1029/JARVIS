import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaTextBlock, BetaThinkingBlock, BetaToolUseBlock
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.models import Base
from app.security.permissions import PermissionLevel


def text_block(text):
    return BetaTextBlock(type="text", text=text)


def thinking_block(signature):
    return BetaThinkingBlock(type="thinking", thinking="", signature=signature)


def tool_use(id, name, input):
    return BetaToolUseBlock(type="tool_use", id=id, name=name, input=input)


def response(stop_reason, *content):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), model="fake",
                           usage=None, stop_details=None)


class FakeClient:
    """Stands in for anthropic.Anthropic: returns scripted responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Snapshot the message list: the agent keeps appending to the same list afterwards.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


@pytest.fixture
def engine():
    """In-memory SQLite by default; set JARVIS_TEST_DATABASE_URL to run against PostgreSQL."""
    url = os.getenv("JARVIS_TEST_DATABASE_URL")
    if url:
        engine = create_engine(url)
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine("sqlite://", poolclass=StaticPool,
                               connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    if url:
        Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        model="claude-opus-5-5",
        effort="medium",
        max_tokens=1000,
        max_agent_steps=5,
        allowed_roots=(tmp_path.resolve(),),
        auto_approve_up_to=PermissionLevel.LOCAL_READ,
        database_url="unused-in-tests",
    )
