"""The JARVIS agent: Claude + tool registry + policy engine, in a manual tool-use loop.

The loop is manual (not the SDK tool runner) because a tool call that needs approval has
to pause the turn across HTTP requests: we stash the pending calls, return to the client,
and resume when the user posts their decisions.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

import anthropic

from app.agent.context import Conversation, PendingCall
from app.config import Settings
from app.security.audit import AuditLog
from app.security.permissions import Decision, PolicyEngine
from app.tools.base import ToolError
from app.tools.registry import ToolRegistry

SYSTEM_PROMPT = """You are JARVIS, a personal AI assistant running on the user's own computer.

You can use tools to look things up, do exact calculations, check the time, and work with the \
user's files. Use them whenever they give a better answer than guessing. File access is limited \
to these folders: {roots}.

Some actions (such as writing files) need the user's approval before they run. If the user \
declines a tool call, accept that and continue without it - do not retry the same call.

Be concise and direct, like a capable assistant who respects the user's time."""

FALLBACK_BETA = "server-side-fallback-2026-07-01"

Status = Literal["done", "waiting_for_user", "refused", "step_limit"]


class ConversationBusy(Exception):
    """The conversation is waiting for tool approvals, so it can't take a new message yet."""


class NothingPending(Exception):
    """A confirmation arrived, but no tool calls are waiting for one."""


@dataclass
class AgentResult:
    conversation_id: str
    status: Status
    reply: str
    pending: list[PendingCall] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        client: anthropic.Anthropic,
        registry: ToolRegistry,
        policy: PolicyEngine,
        audit: AuditLog,
        settings: Settings,
    ):
        self.client = client
        self.registry = registry
        self.policy = policy
        self.audit = audit
        self.settings = settings
        self.system_prompt = SYSTEM_PROMPT.format(
            roots=", ".join(str(r) for r in settings.allowed_roots)
        )

    # ---- public API -----------------------------------------------------------------------

    def send(self, conversation: Conversation, text: str) -> AgentResult:
        if conversation.pending:
            raise ConversationBusy()
        self.audit.record(conversation.id, "user_message", text=text)
        conversation.messages.append({"role": "user", "content": text})
        return self._run(conversation)

    def resolve(self, conversation: Conversation, decisions: dict[str, bool]) -> AgentResult:
        """Apply the user's approve/deny decisions to pending tool calls, then resume the turn."""
        if not conversation.pending:
            raise NothingPending()
        missing = [c.id for c in conversation.pending if c.id not in decisions]
        if missing:
            raise ValueError(f"Missing decisions for tool calls: {', '.join(missing)}")

        results = dict(conversation.held_results)
        for call in conversation.pending:
            approved = decisions[call.id]
            self.audit.record(conversation.id, "user_decision", tool=call.tool,
                              tool_use_id=call.id, approved=approved)
            if approved:
                results[call.id] = self._execute(conversation, call.id, call.tool, call.input)
            else:
                results[call.id] = _tool_result(call.id, "The user declined this action.", is_error=True)

        conversation.messages.append(
            {"role": "user", "content": [results[i] for i in conversation.tool_use_order]}
        )
        conversation.pending = []
        conversation.held_results = {}
        conversation.tool_use_order = []
        return self._run(conversation)

    # ---- loop -----------------------------------------------------------------------------

    def _run(self, conversation: Conversation) -> AgentResult:
        for _ in range(self.settings.max_agent_steps):
            response = self._call_model(conversation.messages)
            self.audit.record(conversation.id, "model_response", stop_reason=response.stop_reason,
                              model=response.model, usage=_usage(response))

            if response.stop_reason == "refusal":
                # The request and its fallback were both declined. Don't keep the empty turn.
                details = getattr(response, "stop_details", None)
                category = getattr(details, "category", None)
                self.audit.record(conversation.id, "refusal", category=category)
                return AgentResult(conversation.id, "refused",
                                   "I can't help with that request.")

            conversation.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                # A server-side tool (web search) hit its iteration limit; re-send to resume.
                continue

            if response.stop_reason != "tool_use":
                reply = _text(response)
                if response.stop_reason == "max_tokens":
                    reply += "\n\n[Response cut off: hit the max_tokens limit.]"
                self.audit.record(conversation.id, "final_response", text=reply)
                return AgentResult(conversation.id, "done", reply)

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            results: dict[str, dict[str, Any]] = {}
            pending: list[PendingCall] = []
            for block in tool_uses:
                tool = self.registry.get(block.name)
                if tool is None:
                    results[block.id] = _tool_result(block.id, f"Unknown tool '{block.name}'.", is_error=True)
                    continue
                decision = self.policy.evaluate(tool.permission)
                self.audit.record(conversation.id, "tool_request", tool=block.name, tool_use_id=block.id,
                                  input=block.input, permission=tool.permission.name,
                                  decision=decision.value)
                if decision is Decision.ALLOW:
                    results[block.id] = self._execute(conversation, block.id, block.name, block.input)
                else:
                    pending.append(PendingCall(block.id, block.name, dict(block.input), tool.permission.name))

            if pending:
                conversation.pending = pending
                conversation.held_results = results
                conversation.tool_use_order = [b.id for b in tool_uses]
                return AgentResult(conversation.id, "waiting_for_user", _text(response), pending)

            # All results go back in a single user message, in the order Claude asked for them.
            conversation.messages.append({"role": "user", "content": [results[b.id] for b in tool_uses]})

        self.audit.record(conversation.id, "step_limit", steps=self.settings.max_agent_steps)
        return AgentResult(conversation.id, "step_limit",
                           f"I stopped after {self.settings.max_agent_steps} steps without finishing. "
                           "Tell me to continue if you want me to keep going.")

    def _call_model(self, messages: list[dict[str, Any]]):
        return self.client.beta.messages.create(
            model=self.settings.model,
            max_tokens=self.settings.max_tokens,
            system=self.system_prompt,
            tools=self.registry.definitions(),
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.settings.effort},
            cache_control={"type": "ephemeral"},
            # If a safety classifier declines, retry server-side on Anthropic's recommended model.
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def _execute(self, conversation: Conversation, tool_use_id: str, name: str,
                 args: dict[str, Any]) -> dict[str, Any]:
        tool = self.registry.get(name)
        try:
            output = tool.execute(args)
            is_error = False
        except ToolError as e:
            output, is_error = str(e), True
        except Exception as e:  # a bug in a tool shouldn't kill the conversation
            output, is_error = f"Tool '{name}' failed unexpectedly: {type(e).__name__}: {e}", True
        self.audit.record(conversation.id, "tool_result", tool=name, tool_use_id=tool_use_id,
                          is_error=is_error, output=output[:2000])
        return _tool_result(tool_use_id, output, is_error)


def _tool_result(tool_use_id: str, content: str, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        result["is_error"] = True
    return result


def _text(response) -> str:
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def _usage(response) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
    }
