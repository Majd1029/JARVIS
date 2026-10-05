"""Nearest-neighbour search: pgvector on PostgreSQL, plain Python elsewhere (the SQLite tests)."""

import math
from typing import Any, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

T = TypeVar("T")


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def nearest(session: Session, model: type[T], query: list[float], limit: int,
            base: Select[Any] | None = None) -> list[tuple[T, float]]:
    """Rows of `model` closest to `query`, as (row, cosine similarity), best first."""
    base = base if base is not None else select(model)
    column = model.embedding  # type: ignore[attr-defined]
    if session.bind.dialect.name == "postgresql":
        distance = column.cosine_distance(query)
        rows = session.execute(base.add_columns(distance).order_by(distance).limit(limit)).all()
        return [(row, 1.0 - dist) for row, dist in rows]
    scored = [(row, cosine(row.embedding, query)) for row in session.scalars(base)]
    return sorted(scored, key=lambda pair: pair[1], reverse=True)[:limit]
