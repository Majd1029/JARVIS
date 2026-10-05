"""Long-term memory: facts intentionally retained across conversations."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import MemoryRow
from app.memory.embeddings import Embedder
from app.memory.vectors import nearest

DUPLICATE_SIMILARITY = 0.92  # above this, a "new" fact is treated as one we already have


@dataclass
class Fact:
    id: int
    content: str
    created_at: datetime
    score: float | None = None


class FactStore:
    def __init__(self, session_factory: sessionmaker[Session], embedder: Embedder):
        self._sessions = session_factory
        self.embedder = embedder

    def add(self, content: str) -> tuple[Fact, bool]:
        """Store a fact. Returns (fact, created); created is False if a near-duplicate existed."""
        content = content.strip()
        [embedding] = self.embedder.embed_documents([content])
        with self._sessions.begin() as session:
            match = nearest(session, MemoryRow, embedding, limit=1)
            if match and match[0][1] >= DUPLICATE_SIMILARITY:
                row, score = match[0]
                return Fact(row.id, row.content, row.created_at, score), False
            row = MemoryRow(content=content, embedding=embedding)
            session.add(row)
            session.flush()
            session.refresh(row)
            return Fact(row.id, row.content, row.created_at), True

    def all(self) -> list[Fact]:
        with self._sessions() as session:
            rows = session.scalars(select(MemoryRow).order_by(MemoryRow.id)).all()
            return [Fact(r.id, r.content, r.created_at) for r in rows]

    def delete(self, fact_id: int) -> bool:
        with self._sessions.begin() as session:
            return session.execute(delete(MemoryRow).where(MemoryRow.id == fact_id)).rowcount > 0

    def search(self, query_embedding: list[float], limit: int, min_score: float) -> list[Fact]:
        with self._sessions() as session:
            return [
                Fact(row.id, row.content, row.created_at, score)
                for row, score in nearest(session, MemoryRow, query_embedding, limit)
                if score >= min_score
            ]
