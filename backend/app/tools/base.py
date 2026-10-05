"""The Tool contract: everything the agent needs to know about a capability."""

from dataclasses import dataclass
from typing import Any, Callable

from app.security.permissions import PermissionLevel


class ToolError(Exception):
    """An expected failure whose message is safe to show to the model (bad path, bad input...)."""


@dataclass(frozen=True)
class Tool:
    name: str  # must match ^[a-zA-Z0-9_-]{1,64}$ (no dots)
    description: str
    input_schema: dict[str, Any]
    permission: PermissionLevel
    handler: Callable[..., str]

    def to_api(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }

    def execute(self, args: dict[str, Any]) -> str:
        return self.handler(**args)
