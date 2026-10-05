"""Chat endpoints: send a message, approve/deny pending tool calls, read the execution trace."""

from contextlib import contextmanager
from typing import Any

import anthropic
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent.agent import Agent, AgentResult, ConversationBusy, NothingPending
from app.agent.context import Conversation, ConversationStore

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None


class ConfirmRequest(BaseModel):
    decisions: dict[str, bool] = Field(description="tool_use_id -> approve (true) / deny (false)")


class PendingCallOut(BaseModel):
    id: str
    tool: str
    input: dict[str, Any]
    permission: str


class ChatResponse(BaseModel):
    conversation_id: str
    status: str
    reply: str
    pending: list[PendingCallOut] = []


def _agent(request: Request) -> Agent:
    return request.app.state.agent


def _store(request: Request) -> ConversationStore:
    return request.app.state.store


def _get_conversation(request: Request, conversation_id: str) -> Conversation:
    conversation = _store(request).get(conversation_id)
    if conversation is None:
        raise HTTPException(404, f"Conversation '{conversation_id}' not found")
    return conversation


@contextmanager
def _exclusive(conversation: Conversation):
    if not conversation.lock.acquire(blocking=False):
        raise HTTPException(409, "This conversation is already processing a request.")
    try:
        yield
    except anthropic.AuthenticationError:
        raise HTTPException(401, "Anthropic API authentication failed. Check ANTHROPIC_API_KEY.")
    except TypeError as e:
        # The SDK raises a bare TypeError when no credentials are configured at all.
        if "authentication" in str(e):
            raise HTTPException(401, "No Anthropic credentials found. Set ANTHROPIC_API_KEY in backend/.env.")
        raise
    except anthropic.RateLimitError:
        raise HTTPException(429, "Rate limited by the Anthropic API. Try again shortly.")
    except anthropic.BadRequestError as e:
        raise HTTPException(400, f"Anthropic API rejected the request: {e.message}")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Anthropic API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(503, "Could not reach the Anthropic API. Check your connection.")
    finally:
        conversation.lock.release()


def _to_response(result: AgentResult) -> ChatResponse:
    return ChatResponse(
        conversation_id=result.conversation_id,
        status=result.status,
        reply=result.reply,
        pending=[PendingCallOut(**vars(p)) for p in result.pending],
    )


# Plain `def` endpoints: FastAPI runs them in a thread pool, so the blocking SDK calls are fine.

@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request) -> ChatResponse:
    if body.conversation_id:
        conversation = _get_conversation(request, body.conversation_id)
    else:
        conversation = _store(request).create()
    with _exclusive(conversation):
        try:
            return _to_response(_agent(request).send(conversation, body.message))
        except ConversationBusy:
            raise HTTPException(409, "Approve or deny the pending tool calls first "
                                     f"(POST /chat/{conversation.id}/confirm).")


@router.post("/chat/{conversation_id}/confirm", response_model=ChatResponse)
def confirm(conversation_id: str, body: ConfirmRequest, request: Request) -> ChatResponse:
    conversation = _get_conversation(request, conversation_id)
    with _exclusive(conversation):
        try:
            return _to_response(_agent(request).resolve(conversation, body.decisions))
        except NothingPending:
            raise HTTPException(409, "No tool calls are waiting for approval.")
        except ValueError as e:
            raise HTTPException(422, str(e))


@router.get("/chat/{conversation_id}/trace")
def trace(conversation_id: str, request: Request) -> list[dict[str, Any]]:
    _get_conversation(request, conversation_id)
    return _agent(request).audit.trace(conversation_id)


@router.get("/tools")
def tools(request: Request) -> list[dict[str, Any]]:
    return _agent(request).registry.describe()
