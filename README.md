# JARVIS

A personal AI assistant built as an agent platform: **Claude = brain · tools = hands · memory = context · permissions = safety**.

This is the MVP (roadmap steps 1–2 plus a basic permission layer): a FastAPI backend, Claude (`claude-opus-5-5`), a tool registry, and a policy engine that asks before risky actions.

## Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env      # then put your key in ANTHROPIC_API_KEY
```

Get an API key at https://platform.claude.com/.

## Run

```bash
uvicorn app.main:app --reload
```

Then, in a second terminal, chat from the terminal client:

```bash
python cli.py
```

Or use the interactive API docs at http://127.0.0.1:8000/docs.

## API

| Endpoint | Purpose |
|---|---|
| `POST /chat` `{message, conversation_id?}` | Send a message. Omit `conversation_id` to start a new conversation. |
| `POST /chat/{id}/confirm` `{decisions: {tool_use_id: bool}}` | Approve or deny the tool calls that are waiting. |
| `GET /chat/{id}/trace` | Execution trace: requests, tool calls, permission decisions, results. |
| `GET /tools` | Registered tools and their permission levels. |
| `GET /health` | Liveness check. |

A `/chat` response has a `status`:
- `done`: `reply` holds the answer.
- `waiting_for_user`: `pending` lists tool calls that need approval. Answer through `/confirm`.
- `refused` or `step_limit`: the turn ended early, and `reply` says why.

## Tools

| Tool | Level | Runs without asking? |
|---|---|---|
| `calculator` | 0 public read | yes |
| `get_current_datetime` | 0 public read | yes |
| `web_search` (Anthropic-hosted) | 0 public read | yes |
| `list_directory`, `read_file` | 1 local read | yes |
| `write_file` | 2 local write | **asks** |

File tools only work inside `JARVIS_ALLOWED_ROOTS`, which defaults to your home folder. Paths outside it, including `..` tricks and symlinks, are refused. `JARVIS_AUTO_APPROVE_LEVEL` (default `1`) sets the highest level that runs without asking. Levels 4 (external communications) and 5 (financial/security) always ask, whatever the setting.

### Adding a tool

1. Create a `Tool` with a name, description, JSON schema, `PermissionLevel` and handler (see `app/tools/calculator.py`).
2. Register it in `build_default_registry` (`app/tools/registry.py`).
3. For expected failures, raise `ToolError("message for the model")`.

## Layout

```
backend/
  app/
    main.py              FastAPI app factory
    config.py            settings from env / .env
    api/routes/chat.py   HTTP endpoints
    agent/agent.py       tool-use loop + approval pause/resume
    agent/context.py     conversation state (in-memory for now)
    tools/               Tool contract, registry, built-in tools
    security/            permission levels, policy engine, audit log
  cli.py                 terminal client
  tests/                 pytest suite (uses a fake Claude client, no API calls)
```

Each audit event is appended to `backend/data/audit.jsonl`.

## Tests

```bash
cd backend
pytest
```

## Next steps (from the roadmap)

3. PostgreSQL: persist conversations, replacing the in-memory `ConversationStore`
4. Memory: long-term facts + semantic search (pgvector)
5. Browser control (Playwright)
6. Screenshots + computer control
7. Voice (speech-to-text / text-to-speech)
8. Scheduler and events
