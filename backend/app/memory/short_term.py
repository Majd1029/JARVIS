"""Short-term memory: conversation history and tool calls awaiting approval, stored in PostgreSQL."""

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ConversationRow, MessageRow


@dataclass
class PendingCall:
    id: str
    tool: str
    input: dict[str, Any]
    permission: str


@dataclass
class Pending:
    calls: list[PendingCall]
    held_results: dict[str, dict[str, Any]]  # results of the auto-approved calls from the same turn
    tool_use_order: list[str]  # results must go back in the order Claude asked for them

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Pending":
        return cls(
            calls=[PendingCall(**c) for c in data["calls"]],
            held_results=data["held_results"],
            tool_use_order=data["tool_use_order"],
        )


@dataclass
class Conversation:
    id: str
    title: str | None = None
    # Append-only: earlier turns (including thinking blocks) are never edited.
    messages: list[dict[str, Any]] = field(default_factory=list)
    pending: Pending | None = None


@dataclass
class ConversationSummary:
    id: str
    title: str | None
    message_count: int
    waiting_for_user: bool
    created_at: datetime
    updated_at: datetime


class ConversationStore:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._sessions = session_factory
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def lock(self, conversation_id: str) -> threading.Lock:
        """Per-conversation lock, so two requests can't run the same conversation at once.

        In-process only: run a single server worker (the default for uvicorn).
        """
        with self._locks_guard:
            return self._locks.setdefault(conversation_id, threading.Lock())

    def create(self) -> Conversation:
        conversation = Conversation(id=uuid.uuid4().hex[:12])
        with self._sessions.begin() as session:
            session.add(ConversationRow(id=conversation.id))
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        with self._sessions() as session:
            row = session.get(ConversationRow, conversation_id)
            if row is None:
                return None
            messages = session.scalars(
                select(MessageRow).where(MessageRow.conversation_id == conversation_id).order_by(MessageRow.seq)
            ).all()
            return Conversation(
                id=row.id,
                title=row.title,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                pending=Pending.from_json(row.pending) if row.pending else None,
            )

    def append_message(self, conversation: Conversation, message: dict[str, Any],
                       pending: Pending | None = None) -> None:
        """Persist one message and, in the same transaction, set (or clear) the pending tool calls.

        Writing both together means a crash can never leave tool calls awaiting approval
        without the assistant turn that asked for them, or vice versa.
        """
        values: dict[str, Any] = {
            "pending": pending.to_json() if pending else None,
            "updated_at": func.now(),
        }
        title = conversation.title
        if title is None and message["role"] == "user" and isinstance(message["content"], str):
            title = values["title"] = message["content"][:200]

        with self._sessions.begin() as session:
            session.add(MessageRow(
                conversation_id=conversation.id,
                seq=len(conversation.messages),
                role=message["role"],
                content=message["content"],
            ))
            session.execute(update(ConversationRow).where(ConversationRow.id == conversation.id).values(**values))

        conversation.messages.append(message)
        conversation.pending = pending
        conversation.title = title

    def list(self, limit: int = 50) -> list[ConversationSummary]:
        counts = (
            select(MessageRow.conversation_id, func.count().label("n"))
            .group_by(MessageRow.conversation_id)
            .subquery()
        )
        query = (
            select(ConversationRow, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.conversation_id == ConversationRow.id)
            .order_by(ConversationRow.updated_at.desc())
            .limit(limit)
        )
        with self._sessions() as session:
            return [
                ConversationSummary(
                    id=row.id,
                    title=row.title,
                    message_count=count,
                    waiting_for_user=row.pending is not None,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row, count in session.execute(query)
            ]
