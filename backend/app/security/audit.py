"""Execution trace: every request, tool call, permission decision and result, stored in PostgreSQL."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AuditEventRow


class AuditLog:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._sessions = session_factory

    def record(self, conversation_id: str, event: str, **data: Any) -> None:
        with self._sessions.begin() as session:
            session.add(AuditEventRow(conversation_id=conversation_id, event=event, data=data))

    def trace(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.conversation_id == conversation_id)
                .order_by(AuditEventRow.id)
            ).all()
            return [{"ts": r.created_at.isoformat(), "event": r.event, **r.data} for r in rows]
