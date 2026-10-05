import os
import re
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaTextBlock, BetaThinkingBlock, BetaToolUseBlock
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.models import Base
from app.memory.embeddings import EMBEDDING_DIMS, EmbeddingError
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
        self.messages = SimpleNamespace(create=self._create)  # used for Ollama
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))  # used for Claude

    def _create(self, **kwargs):
        # Snapshot the message list: the agent keeps appending to the same list afterwards.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


class FakeEmbedder:
    """Deterministic bag-of-words vectors: texts sharing words are similar. No Ollama needed."""

    def __init__(self):
        self.available = True

    def _vector(self, text):
        if not self.available:
            raise EmbeddingError("Ollama not reachable for embeddings (fake)")
        vector = [0.0] * EMBEDDING_DIMS
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[zlib.crc32(word.encode()) % EMBEDDING_DIMS] += 1.0
        return vector

    def embed_query(self, text):
        return self._vector(text)

    def embed_documents(self, texts):
        return [self._vector(t) for t in texts]


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def engine():
    """In-memory SQLite by default; set JARVIS_TEST_DATABASE_URL to run against PostgreSQL."""
    url = os.getenv("JARVIS_TEST_DATABASE_URL")
    if url:
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
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
        provider="anthropic",
        model="claude-opus-5-5",
        ollama_url="http://127.0.0.1:11434",
        effort="medium",
        max_tokens=1000,
        max_agent_steps=5,
        allowed_roots=(tmp_path.resolve(),),
        auto_approve_up_to=PermissionLevel.LOCAL_READ,
        database_url="unused-in-tests",
        memory_min_similarity=0.3,  # bag-of-words vectors score lower than real embeddings
    )
