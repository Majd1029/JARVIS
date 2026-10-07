# CLAUDE.md — JARVIS project context

Read this first. It summarizes the project, how it's set up on this machine, the design decisions that must not be broken, and what's next. User-facing setup docs live in `README.md`.

## What this is

A personal J.A.R.V.I.S.-style AI assistant, built as an **agent platform**: LLM = brain, tools = hands, memory = context, event system = reflexes, UI = control panel, permissions = safety. The original plan is a design doc (`C:\Users\majda\Downloads\JARVIS.docx`) with a 12-step roadmap.

- **Owner:** Majd (GitHub `Majd1029`). Repo: https://github.com/Majd1029/JARVIS, branch `main`.
- **Hard constraint: it must run on free resources, with no billing or API credits.** The default model is a local one through Ollama. The Claude API path still exists but needs paid credits, and the account currently has none.
- **Machine:** Windows 11 laptop. Ryzen 7 4800H, 16 GB RAM, RTX 3050 Laptop GPU with **4 GB VRAM**. Python 3.11. Docker Desktop. Ollama 0.35.

## Roadmap status

| Step | Status |
|---|---|
| 1. Chat endpoint (FastAPI + LLM) | ✅ done |
| 2. Tool calling + tool registry + permission layer | ✅ done |
| 3. PostgreSQL persistence (conversations, pending approvals, audit trace) | ✅ done |
| 4. Memory: long-term facts + semantic memory (RAG, pgvector) | ✅ done |
| 5. Browser control (Playwright) | ✅ done |
| 6. Screenshots + computer vision/control | ⏭️ next |
| 7. Voice (speech-to-text / text-to-speech; push-to-talk first, wake word later) | todo |
| 8. Scheduler / events (reminders, cron, webhooks, file watchers) | todo |
| 9. Permissions (basic version already exists; extend as risky tools arrive) | partial |
| 10. Workflow engine for multi-step tasks | todo |
| 11. Frontend dashboard (Next.js + React + TypeScript) | todo |
| 12. Plugins (installable capabilities) | todo |

Commit history: `05ccb9b` MVP → `a883aa5` step 3 → `cd6a56b` .env fix → `c27d293` local Ollama → `e00e81f` step 4 → `7cdfec4` LF normalization → `ea3a611` CLAUDE.md → step 5 (browser) commit.

## Environment on this machine

| Piece | Details |
|---|---|
| Repo | `C:\Users\majda\OneDrive\Desktop\JARVIS` (own git repo, remote `origin`) |
| Python venv | `backend\.venv` (all deps installed). Use `backend\.venv\Scripts\python`. |
| Database | Docker container `jarvis-db`, image `pgvector/pgvector:pg17`, on **127.0.0.1:5433** (5432 is taken by a native PostgreSQL 18 service, left alone). User/password/db `jarvis/jarvis/jarvis`. Volume `jarvis-pgdata`. `restart: unless-stopped`, so it comes back when Docker Desktop starts. |
| Test database | `jarvis_test` in the same container |
| Ollama models | `qwen3:4b-instruct` (base), `jarvis-qwen3` (base + `num_ctx 8192`, built from `ollama/Modelfile`), `nomic-embed-text` (embeddings, 768 dims) |
| Secrets | `backend\.env` (gitignored) holds `ANTHROPIC_API_KEY`. The key is valid, but the account has **no credits**. Never commit `.env`. |
| Server launcher | `.claude/launch.json` config `jarvis-backend` runs uvicorn on port 8000 from the repo root (use `preview_start`). |
| Browser | Playwright drives the **installed Microsoft Edge** (`channel="msedge"`); Chrome is also installed. No `playwright install` download was needed. |

**Important home-folder quirk:** `C:\Users\majda` itself is a git repo, pointing to an unrelated Bitcoin project. JARVIS has its own `.git`, so it's independent. Don't touch the home-folder repo, which has unpushed commits.

## Run / test commands

From `backend\`:
```bash
.venv\Scripts\uvicorn app.main:app --reload     # server on :8000 (API docs at /docs)
.venv\Scripts\python cli.py                      # terminal client: list, open <id>, new, memory, index <path>, exit
.venv\Scripts\python -m pytest -q                # tests on in-memory SQLite (no Ollama needed)
.venv\Scripts\alembic upgrade head               # apply migrations
```
To run the tests on real Postgres, set `JARVIS_TEST_DATABASE_URL=postgresql+psycopg://jarvis:jarvis@127.0.0.1:5433/jarvis_test`.
To rebuild the local model from the repo root: `ollama create jarvis-qwen3 -f ollama/Modelfile`.
For a health check, `GET /health` reports provider, `model_status`, `embedding_model_status` and the database.

**Current state: 48 tests, all passing on both SQLite and PostgreSQL** (about 15 s; the browser tests launch real headless Edge against a local test site and skip if no browser can start). Always run both after changes that touch the database.

## Architecture

```
backend/app/
  main.py               create_app(settings, client, engine, embedder, browser) factory; make_client(); warm_up(); lifespan; /health
  config.py             Settings (frozen dataclass) from env; loads backend/.env by absolute path
  agent/agent.py        Agent: manual tool-use loop, approval pause/resume, memory attachment
  api/routes/chat.py    /chat, /chat/{id}/confirm, /conversations, /conversations/{id}[/trace], /tools
  api/routes/memory.py  /memory/facts, /memory/documents, /memory/search
  memory/short_term.py  ConversationStore (Postgres): messages, pending approvals, per-conversation locks
  memory/long_term.py   FactStore: remembered facts (dedupe at cosine ≥ 0.92)
  memory/semantic.py    DocumentStore: extract (text/code/PDF), chunk, embed, re-index on change
  memory/retrieval.py   Retriever: recall facts + chunks for a query; builds the <memory> block
  memory/embeddings.py  Embedder: Ollama /api/embed, nomic task prefixes, forced to CPU
  memory/vectors.py     nearest(): pgvector cosine_distance on PG, Python cosine on SQLite
  db/models.py          SQLAlchemy models; db/session.py engine + sessionmaker
  tools/base.py         Tool dataclass (name, description, input_schema, permission, handler), ToolError
  tools/registry.py     ToolRegistry + build_default_registry(settings, retriever)
  tools/*.py            calculator, clock, filesystem (Sandbox), web (DuckDuckGo), memory, browser (Playwright)
  security/permissions.py  PermissionLevel 0–5, PolicyEngine (ALLOW / CONFIRM)
  security/audit.py     AuditLog → audit_events table (the execution trace)
backend/migrations/     Alembic (2 revisions: e3dde3b74808 base tables, bff77902a7d8 memory + pgvector)
backend/cli.py          stdlib-only HTTP client for the API
ollama/Modelfile        jarvis-qwen3 definition
docker-compose.yml      Postgres + pgvector
```

### Model providers (`JARVIS_PROVIDER`)
- **`ollama` (default):** the Anthropic Python SDK is pointed at Ollama's Anthropic-compatible Messages API (`base_url=http://127.0.0.1:11434`, `api_key="ollama"`). It's called with plain `client.messages.create(model, max_tokens, system, tools, messages)` and **no Claude-only options**. Web search is a local DuckDuckGo tool (`ddgs`).
- **`anthropic`:** `client.beta.messages.create` with `claude-opus-5-5`, `thinking={"type":"adaptive"}`, `output_config={"effort": ...}`, top-level `cache_control`, and `betas=["server-side-fallback-2026-07-01"]` + `fallbacks="default"`. Web search is the Anthropic server tool `web_search_20260209`.
- Both paths use the same message format and the same stored history.

### Tools and permission levels
Levels: 0 public read, 1 local read, 2 local write, 3 execute, 4 external comms, 5 financial/security. `JARVIS_AUTO_APPROVE_LEVEL` (default 1) is the highest level that runs automatically. `PolicyEngine` caps auto-approval at 3, so levels 4–5 **always** ask.

| Tool | Level | Auto? |
|---|---|---|
| calculator, get_current_datetime, web_search | 0 | yes |
| list_directory (sort_by name/size/modified), read_file | 1 | yes |
| remember, search_memory, index_file | 1 | yes (memory writes only touch JARVIS's own DB) |
| write_file, forget | 2 | **asks** |
| browser_open, browser_read, browser_follow_link, browser_back | 0 | yes |
| browser_click, browser_type (optional press_enter) | 4 | **always asks** |

File tools are sandboxed to `JARVIS_ALLOWED_ROOTS` (default: home folder) via `Sandbox.resolve()`, which resolves symlinks and `..`. Tool names must match `^[a-zA-Z0-9_-]{1,64}$` (no dots). Tools use `strict: True` schemas with `additionalProperties: false`.

### Approval flow
When the model requests a tool that needs confirmation, the agent runs the auto-approved calls from the same turn and stores their results in `Pending.held_results`. It then saves the assistant message together with the pending state, **in one transaction**, and returns `status: waiting_for_user`. `POST /chat/{id}/confirm {decisions: {tool_use_id: bool}}` executes the approved calls, turns denied ones into `is_error` results, appends all results **in the original tool_use order in one user message**, and resumes the loop.

## Invariants: don't break these

1. **History is append-only.** Never edit or delete earlier messages. Stored assistant content is `[block.to_dict(mode="json") for block in response.content]`, exactly as returned, thinking blocks and signatures included. Claude's preserved-thinking check and prompt caching depend on byte-identical replay.
2. **`messages.content` is Postgres `json`, not `jsonb`.** `jsonb` reorders keys. `test_stored_json_keeps_key_order` guards this.
3. **Pending state is saved with the message.** `ConversationStore.append_message(conv, msg, pending=...)` always sets the pending column, and `None` clears it.
4. **Interrupted tool calls get closed.** If history ends with an assistant `tool_use` that has no result (a crash), `_close_interrupted_tool_calls` appends error results before the next user message.
5. **Memory is attached to the user message, not the system prompt.** It goes in as `content = [{"type":"text","text":"<memory>…</memory>"}, {"type":"text","text": user_text}]` and is stored like that. `MEMORY_TAG` marks the block so `GET /conversations/{id}` and titles hide it. If embeddings fail, chat continues without memory and the trace records `memory_unavailable`.
6. **Embeddings run on the CPU** (`options.num_gpu = 0`). The chat model already overflows the 4 GB GPU (about 58% GPU / 42% CPU), and putting the embedder there makes Ollama swap models every message.
7. **The embedding dimension is fixed at 768** (`EMBEDDING_DIMS`), matching `vector(768)` columns. Changing the embedding model requires a migration.
8. **The chunker version is in the content hash** (`CHUNKER_VERSION`). Bump it when `chunk_text` changes, so existing documents get re-indexed.
9. **Conversation locks are in-process.** Run one uvicorn worker. Routes take the lock first, *then* load fresh state from the DB.
10. **Anything Claude-specific stays on the `anthropic` branch** of `Agent._call_model`. Ollama rejects or ignores it.
11. **All Playwright calls run on `BrowserSession`'s single worker thread** (`_run()`). The sync API breaks if it's used from FastAPI's threadpool threads directly.
12. **The browser shows pages as text plus numbered elements.** Elements are tagged in the DOM with `data-jarvis-id`, and tools act by number. Every snapshot re-numbers, and element info comes from the latest snapshot. Keep outputs small: `TEXT_PART_CHARS` 2500, `MAX_ELEMENTS` 30.
13. **Browser safety:** a clean context (no user cookies or logins), `accept_downloads=False`, dialogs dismissed, only `http(s)` URLs (`_validate_url`), and the untrusted-content note on every page. Click and type are level 4 (always ask). A blocked click falls back to a JavaScript `el.click()` (overlays such as search suggestions or cookie banners).

## Database tables
`conversations` (id, title, pending jsonb, timestamps) · `messages` (conversation_id, seq, role, content json; unique(conversation_id, seq)) · `audit_events` (event, data jsonb) · `memories` (content, embedding vector(768)) · `documents` (path unique, content_hash, chunk_count) · `document_chunks` (document_id, chunk_index, content, embedding vector(768)). HNSW cosine indexes are created manually in the migration. Embedding columns are `Vector(768).with_variant(JSON(), "sqlite")`, with Vector as the **base** type so `cosine_distance` exists.

## Testing approach
- `tests/conftest.py`:
  - `FakeClient` returns scripted responses built from real SDK block types (`BetaTextBlock`, `BetaToolUseBlock`, `BetaThinkingBlock`) and records each request.
  - `FakeEmbedder` makes bag-of-words hashed vectors, so texts sharing words are similar. `available=False` simulates an Ollama outage.
  - The `engine` fixture is SQLite in-memory, or Postgres if `JARVIS_TEST_DATABASE_URL` is set; it enables the `vector` extension on PG.
  - `settings` uses `provider="anthropic"` and `memory_min_similarity=0.3`.
- Tests never call real models. Test files: `test_tools.py`, `test_agent.py`, `test_persistence.py` (restart survival, replay equality), `test_memory.py`.
- For live verification, start the server and curl `/chat`. Afterwards, **delete test data** (`DELETE FROM conversations/memories/documents`), and never leave invented "facts" about the user in memory.

## Measured performance and limits (local model)
- Warm tool-call step: about 2.6 s. Simple two-tool question: about 15 s, including model load. Answers using attached document chunks: about 28 s. Open and summarize a web page: about 12–16 s. First call after 5 minutes idle adds about 10 s (model reload).
- **Cold start after a long idle measured 89 s** (2.5 GB read from disk plus full prompt processing). `warm_up()` runs in a background thread at server start (Ollama provider only, skipped when tests inject a client) and takes about 13 s.
- Plain `qwen3:4b` (the thinking variant) was rejected. It took about 36 s per step, and with thinking "disabled" it wrote its reasoning into the visible text anyway. Use the **Instruct** variant.
- A 4B model is weak at multi-step reasoning, ranking long lists, and judging stale sources. **Put computation in tools** (as with `list_directory` `sort_by`) instead of asking the model to do it.
- The 8K context means Ollama drops the oldest context in long conversations.
- The model sometimes calls `search_memory` even when the answer is already attached, which costs an extra step.
- Contradicting facts aren't merged. A new fact is stored alongside the old one.

## Known gotchas
- **Uvicorn `--reload` sometimes misses changes.** After editing, stop and start the preview server, then check `/health`.
- **Line endings:** the repo is LF and enforced by `.gitattributes`. Python's `Path.write_text()` on Windows writes CRLF. When scripting edits, use `open(f, "w", newline="\n")` or the Edit/Write tools. Also avoid heredoc Python scripts that embed `\n` inside replacement strings: twice that silently corrupted strings or left edits unapplied. Prefer the Edit tool.
- **Never name a method `list`** inside a class whose later annotations use `list[...]`. It shadows the builtin (hence `FactStore.all()` / `DocumentStore.all()`).
- The SDK raises a bare `TypeError("Could not resolve authentication method…")` when no Anthropic credentials exist. The chat route maps it to 401.
- `ddgs` search is occasionally empty on the first try, so `search_web` retries once.
- The system clock and timezone report Turkey Standard Time (UTC+3). `get_current_datetime` uses local time.
- The small model tends to type and then click a separate Search button (two approvals) rather than use `press_enter=True`.
- Heredoc Python with nested brackets in `-c` one-liners is error-prone. Write a temp script file under `$TEMP` instead.

## Working conventions with Majd
- **Commits:** only when asked ("commit and push"). **No Claude attribution:** no `Co-Authored-By: Claude` trailer, no "Generated with Claude Code"; only Majd appears as author. Use descriptive multi-line commit messages. Before pushing, check `git diff --cached | grep -c sk-ant` is 0.
- Ask before large downloads, deleting things, or decisions with real tradeoffs (Majd chose local Ollama over free cloud tiers).
- Majd prefers being told plainly what happened, including mistakes. For example: the API key was once pasted into `.env.example`, which git tracks, and was moved to `.env` before any commit.

## Suggested next step: roadmap step 6 (screenshots + computer vision/control)
The design doc's loop: screenshot → understand the UI → decide → click/type → screenshot again → verify. Constraints to plan around:
- **Vision needs a vision model.** `qwen3:4b-instruct` is text-only. Free local options in Ollama include `qwen2.5vl:3b` and `gemma3:4b` (both vision-capable), but the GPU has only 4 GB and the chat model already overflows it. Measure load and swap times before committing; one model handling both text and vision may be better than two.
- **Screenshots:** `mss` or Pillow `ImageGrab` for the desktop; Playwright `page.screenshot()` for the browser. Downscale before sending (token cost and speed).
- **Desktop control:** `pyautogui` (or `pywinauto` for Windows UI automation, which can read UI elements as text, a better fit for a small model, like the browser's numbered elements). Clicking and typing on the real desktop must be at least level 3 and should always ask at first.
- Safety: never act on text seen in screenshots as instructions; keep the policy engine on every action; consider an emergency stop.
