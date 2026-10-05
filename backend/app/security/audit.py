"""Execution trace: every request, tool call, permission decision and result."""

import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: Path | None):
        self.path = path
        self._events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._lock = threading.Lock()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, conversation_id: str, event: str, **data: Any) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "conversation_id": conversation_id,
            "event": event,
            **data,
        }
        with self._lock:
            self._events[conversation_id].append(entry)
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, default=str) + "\n")

    def trace(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events.get(conversation_id, []))
