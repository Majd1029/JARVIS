"""Short-term memory: conversation history and any tool calls awaiting the user's approval.

In-memory for the MVP; roadmap step 3 moves this to PostgreSQL.
"""

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingCall:
    id: str
    tool: str
    input: dict[str, Any]
    permission: str


@dataclass
class Conversation:
    id: str
    # Append-only: earlier turns (including thinking blocks) are never edited.
    messages: list[dict[str, Any]] = field(default_factory=list)
    # When the model's last turn asked for tools that need approval:
    pending: list[PendingCall] = field(default_factory=list)
    held_results: dict[str, dict[str, Any]] = field(default_factory=dict)  # results of auto-approved calls
    tool_use_order: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class ConversationStore:
    def __init__(self):
        self._conversations: dict[str, Conversation] = {}
        self._lock = threading.Lock()

    def create(self) -> Conversation:
        conversation = Conversation(id=uuid.uuid4().hex[:12])
        with self._lock:
            self._conversations[conversation.id] = conversation
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        with self._lock:
            return self._conversations.get(conversation_id)
