"""Tool registry: the single place the agent learns what it can do."""

from typing import Any, Iterator

from app.config import Settings
from app.security.permissions import PermissionLevel
from app.tools.base import Tool
from app.tools.calculator import calculator_tool
from app.tools.clock import clock_tool
from app.tools.filesystem import Sandbox, build_filesystem_tools

# Anthropic-hosted web search: runs on Anthropic's servers, results come back in the same response.
WEB_SEARCH_TOOL: dict[str, Any] = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}


class ToolRegistry:
    def __init__(self, server_tools: list[dict[str, Any]] | None = None):
        self._tools: dict[str, Tool] = {}
        self.server_tools = server_tools or []

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __iter__(self) -> Iterator[Tool]:
        return iter(sorted(self._tools.values(), key=lambda t: t.name))

    def definitions(self) -> list[dict[str, Any]]:
        # Deterministic order keeps the prompt-cache prefix stable between requests.
        return [t.to_api() for t in self] + self.server_tools

    def describe(self) -> list[dict[str, Any]]:
        tools = [
            {"name": t.name, "description": t.description, "permission": t.permission.name,
             "level": int(t.permission), "runs": "local"}
            for t in self
        ]
        tools += [
            {"name": s["name"], "description": "Anthropic-hosted server tool.",
             "permission": PermissionLevel.PUBLIC_READ.name, "level": 0, "runs": "server"}
            for s in self.server_tools
        ]
        return tools


def build_default_registry(settings: Settings) -> ToolRegistry:
    registry = ToolRegistry(server_tools=[WEB_SEARCH_TOOL])
    registry.register(calculator_tool)
    registry.register(clock_tool)
    for tool in build_filesystem_tools(Sandbox(settings.allowed_roots)):
        registry.register(tool)
    return registry
