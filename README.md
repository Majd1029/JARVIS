# JARVIS

A personal AI assistant built as an agent platform: **LLM = brain · tools = hands · memory = context · permissions = safety**.

Current state: roadmap steps 1–4 plus a basic permission layer:
- FastAPI backend running a **free local model** through Ollama (Qwen3 4B Instruct by default). Claude can be switched on instead.
- A tool registry, and a policy engine that asks before risky actions
- Conversations, pending approvals and the execution trace stored in PostgreSQL
- Long-term memory (facts) and semantic memory (search over your documents), with pgvector

## Setup

Requirements: Python 3.11+, Docker Desktop and [Ollama](https://ollama.com/download). Everything runs locally, for free.

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
   copy .env.example .env
   ```

3. Create the tables:

   ```bash
   alembic upgrade head
   ```

4. Set up the local models (Ollama). Run these from the repo root. First the chat model, about 2.5 GB:

   ```bash
   ollama pull qwen3:4b-instruct
   ```

   ```bash
   ollama create jarvis-qwen3 -f ollama/Modelfile
   ```

   The second command builds `jarvis-qwen3`, which is the same model with a context window big enough for JARVIS's tools and history. Then the embedding model used for memory, about 270 MB:

   ```bash
   ollama pull nomic-embed-text
   ```

   `GET /health` reports `model_status: ok` and `embedding_model_status: ok` once both are ready.

### Choosing the model

`JARVIS_PROVIDER` in `backend/.env` picks what runs JARVIS:

| Provider | Cost | Notes |
|---|---|---|
| `ollama` (default) | Free | Runs on your machine, private, works offline. A 4B model is much less capable than Claude: fine for simple questions and single tool calls, less reliable on multi-step tasks. Web search uses free DuckDuckGo. |
| `anthropic` | Paid API credits | Claude (`claude-opus-5-5`). Set `ANTHROPIC_API_KEY`. Web search is Anthropic-hosted. |

Memory embeddings always come from local Ollama, whichever provider you choose; Anthropic has no embeddings API.

Expect a few seconds to tens of seconds per reply on a 4 GB laptop GPU, plus a one-time load when the model hasn't been used for 5 minutes. A GPU with more VRAM can run a bigger model: pull it, change `FROM` in `ollama/Modelfile` and re-run `ollama create`.

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
- `memory` shows remembered facts and indexed documents.
- `index <path>` adds a file to searchable memory.
- `exit` quits.

You can also use the interactive API docs at http://127.0.0.1:8000/docs.

## Memory

JARVIS has three kinds of memory:

| Kind | What it holds | How it's used |
|---|---|---|
| Short-term | The current conversation | Sent with every request |
| Long-term | Facts worth keeping ("Majd prefers Python for backend work") | Saved with the `remember` tool, when you share something or ask it to remember |
| Semantic | Your documents (PDF, text, Markdown, code), split into ~1,200-character chunks | Indexed with `index <path>`, the `index_file` tool or `POST /memory/documents` |

**Relevant memory is attached automatically.** For each message, JARVIS embeds what you wrote, finds the closest facts (up to 5) and document chunks (up to 3), and attaches them to the message in a `<memory>` block. Only matches above `JARVIS_MEMORY_MIN_SIMILARITY` (default 0.55) are attached. This way even a small model that wouldn't think to search still uses what it knows. The block is stored with the message, so history replays exactly. It's hidden from `GET /conversations/{id}`, and each recall is logged in the trace.

**Embeddings run on the CPU.** The chat model needs the whole 4 GB GPU, and if the embedding model loaded there too, Ollama would swap models on every message. On the CPU, embedding a message takes about 0.2 s.

**What you can control:**
- `remember` runs without asking, since it only writes to JARVIS's own memory, never your files. You can review everything with `GET /memory/facts` or `memory` in the CLI.
- `forget` asks for approval first.
- Re-indexing an unchanged file does nothing. Indexing a changed file replaces its old chunks.

## API

| Endpoint | Purpose |
|---|---|
| `POST /chat` `{message, conversation_id?}` | Send a message. Omit `conversation_id` to start a new conversation. |
| `POST /chat/{id}/confirm` `{decisions: {tool_use_id: bool}}` | Approve or deny the tool calls that are waiting. |
| `GET /conversations` | Saved conversations, most recent first. |
| `GET /conversations/{id}` | Readable message history, plus any tool calls waiting for approval. |
| `GET /conversations/{id}/trace` | Execution trace: requests, memory recalls, tool calls, permission decisions, results. |
| `GET /memory/facts`, `POST /memory/facts` `{content}`, `DELETE /memory/facts/{id}` | Review, add and delete remembered facts. |
| `GET /memory/documents`, `POST /memory/documents` `{path}`, `DELETE /memory/documents/{id}` | List, index and remove documents. |
| `GET /memory/search?q=...` | Search facts and documents. |
| `GET /tools` | Registered tools and their permission levels. |
| `GET /health` | Liveness check: provider, model and embedding-model status, database connectivity. |

A `/chat` response has a `status`:
- `done`: `reply` holds the answer.
- `waiting_for_user`: `pending` lists tool calls that need approval. Answer through `/confirm`.
- `refused` or `step_limit`: the turn ended early, and `reply` says why.

## Tools

| Tool | Level | Runs without asking? |
|---|---|---|
| `calculator` | 0 public read | yes |
| `get_current_datetime` | 0 public read | yes |
| `web_search` (DuckDuckGo locally, Anthropic-hosted with Claude) | 0 public read | yes |
| `list_directory`, `read_file` | 1 local read | yes |
| `remember`, `search_memory`, `index_file` | 1 local read | yes |
| `write_file` | 2 local write | **asks** |
| `forget` | 2 local write | **asks** |

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
| `memories` | Long-term facts and their embeddings |
| `documents`, `document_chunks` | Indexed files and their embedded chunks |

The embedding columns are pgvector `vector(768)` with HNSW indexes for cosine similarity.

**Messages are stored exactly as the model returned them,** including thinking blocks. The `content` column uses Postgres `json` rather than `jsonb`, because `jsonb` reorders keys. That way, history replayed after a restart is identical to what was originally sent.

**State changes are saved together.** Each message is saved in the same transaction as the change to the conversation's pending approvals. If the server stops between the model requesting a tool and its result being saved, the next message answers that call with an "interrupted" error, which keeps the history valid.

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
ollama/Modelfile         local model definition (jarvis-qwen3)
backend/
  app/
    main.py              FastAPI app factory
    config.py            settings from env / .env
    api/routes/          HTTP endpoints (chat, memory)
    agent/agent.py       tool-use loop + approval pause/resume + memory attachment
    memory/
      short_term.py      conversation store
      long_term.py       remembered facts
      semantic.py        document indexing and chunking
      retrieval.py       finds relevant memory for a message
      embeddings.py      Ollama embedding client
    db/                  SQLAlchemy models and engine
    tools/               Tool contract, registry, built-in tools
    security/            permission levels, policy engine, audit log
  migrations/            Alembic migrations
  cli.py                 terminal client
  tests/                 pytest suite (fake model and embeddings, no Ollama needed)
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

5. Browser control (Playwright)
6. Screenshots + computer control
7. Voice (speech-to-text / text-to-speech)
8. Scheduler and events
