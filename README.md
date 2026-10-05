# JARVIS

A personal AI assistant built as an agent platform: **Claude = brain · tools = hands · memory = context · permissions = safety**.

Current state: roadmap steps 1–3 plus a basic permission layer:
- FastAPI backend with Claude (`claude-opus-5-5`)
- A tool registry, and a policy engine that asks before risky actions
- Conversations, pending approvals and the execution trace stored in PostgreSQL

## Setup

Requirements: Python 3.11+ and Docker Desktop.

1. Start the database. Run this from the repo root:

   ```bash
   docker compose up -d
   ```

   This starts PostgreSQL 17 with pgvector on `127.0.0.1:5433`, so it doesn't clash with a local PostgreSQL on 5432. Data lives in the `jarvis-pgdata` Docker volume.

2. Install the backend:

   ```bash
   cd backend
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements-dev.txt
   copy .env.example .env      # then put your key in ANTHROPIC_API_KEY
   ```

   Get an API key at https://platform.claude.com/.

3. Create the tables:

   ```bash
   alembic upgrade head
   ```

## Run

```bash
uvicorn app.main:app --reload
```

Then, in a second terminal, chat from the terminal client:

```bash
python cli.py
```

Inside the client:
- `list` shows saved conversations.
- `open <id>` resumes one, including any approvals that were still waiting.
- `new` starts a fresh conversation.
- `exit` quits.

You can also use the interactive API docs at http://127.0.0.1:8000/docs.

## API

| Endpoint | Purpose |
|---|---|
| `POST /chat` `{message, conversation_id?}` | Send a message. Omit `conversation_id` to start a new conversation. |
| `POST /chat/{id}/confirm` `{decisions: {tool_use_id: bool}}` | Approve or deny the tool calls that are waiting. |
| `GET /conversations` | Saved conversations, most recent first. |
| `GET /conversations/{id}` | Readable message history, plus any tool calls waiting for approval. |
| `GET /conversations/{id}/trace` | Execution trace: requests, tool calls, permission decisions, results. |
| `GET /tools` | Registered tools and their permission levels. |
| `GET /health` | Liveness check, including database connectivity. |

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

## Database

| Table | Contents |
|---|---|
| `conversations` | id, title (first message), tool calls awaiting approval, timestamps |
| `messages` | The full Messages API history, one row per message, in order |
| `audit_events` | The execution trace |

**Messages are stored exactly as Claude returned them,** including thinking blocks. The `content` column uses Postgres `json` rather than `jsonb`, because `jsonb` reorders keys. That way, history replayed after a restart is identical to what was originally sent.

**State changes are saved together.** Each message is saved in the same transaction as the change to the conversation's pending approvals. If the server stops between Claude requesting a tool and its result being saved, the next message answers that call with an "interrupted" error, which keeps the history valid.

To change the schema, edit `app/db/models.py` and then run:

```bash
alembic revision --autogenerate -m "describe the change"
```

```bash
alembic upgrade head
```

Note: conversation locking happens in-process, so run a single server worker (the uvicorn default).

## Layout

```
docker-compose.yml       PostgreSQL + pgvector
backend/
  app/
    main.py              FastAPI app factory
    config.py            settings from env / .env
    api/routes/chat.py   HTTP endpoints
    agent/agent.py       tool-use loop + approval pause/resume
    memory/short_term.py conversation store (PostgreSQL)
    db/                  SQLAlchemy models and engine
    tools/               Tool contract, registry, built-in tools
    security/            permission levels, policy engine, audit log
  migrations/            Alembic migrations
  cli.py                 terminal client
  tests/                 pytest suite (fake Claude client, no API calls)
```

## Tests

```bash
cd backend
pytest
```

By default the tests use in-memory SQLite. To run them against PostgreSQL instead, create a `jarvis_test` database and set `JARVIS_TEST_DATABASE_URL`:

```bash
docker exec jarvis-db psql -U jarvis -c "CREATE DATABASE jarvis_test"
```

```bash
set JARVIS_TEST_DATABASE_URL=postgresql+psycopg://jarvis:jarvis@127.0.0.1:5433/jarvis_test
```

```bash
pytest
```

## Next steps (from the roadmap)

4. Memory: long-term facts + semantic search (pgvector)
5. Browser control (Playwright)
6. Screenshots + computer control
7. Voice (speech-to-text / text-to-speech)
8. Scheduler and events
