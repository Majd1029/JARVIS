"""Memory tools: remember facts, search memory, forget, and index files for retrieval."""

from app.memory.retrieval import Retriever
from app.security.permissions import PermissionLevel
from app.tools.base import Tool, ToolError
from app.tools.filesystem import Sandbox


def build_memory_tools(retriever: Retriever, sandbox: Sandbox) -> list[Tool]:
    facts, documents = retriever.facts, retriever.documents

    def remember(fact: str) -> str:
        if not fact.strip():
            raise ToolError("Nothing to remember: the fact is empty.")
        stored, created = facts.add(fact)
        if not created:
            return f"Already remembered (memory #{stored.id}): {stored.content}"
        return f"Remembered (memory #{stored.id}): {stored.content}"

    def search_memory(query: str) -> str:
        found = retriever.recall(query, fact_limit=10, chunk_limit=5)
        if not found:
            return f"Nothing relevant in memory for '{query}'."
        return Retriever.context_block(found)

    def forget(memory_id: int) -> str:
        if not facts.delete(memory_id):
            raise ToolError(f"There is no memory #{memory_id}.")
        return f"Forgot memory #{memory_id}."

    def index_file(path: str) -> str:
        document, status = documents.index(sandbox.resolve(path))
        if status == "unchanged":
            return f"{document.path} is already indexed and hasn't changed ({document.chunk_count} parts)."
        return f"{status.capitalize()} {document.path} ({document.chunk_count} parts). It's now searchable."

    return [
        Tool(
            name="remember",
            description=(
                "Save a lasting fact about the user or their world (preferences, people, projects, "
                "decisions) so you know it in future conversations. Use it when the user shares "
                "something worth keeping or asks you to remember. One short, self-contained fact per call."
            ),
            input_schema={
                "type": "object",
                "properties": {"fact": {"type": "string", "description": "e.g. 'Majd prefers Python for backend work.'"}},
                "required": ["fact"],
                "additionalProperties": False,
            },
            # Writes only to JARVIS's own memory (reviewable and deletable at GET /memory/facts),
            # never to the user's files, so it runs without asking.
            permission=PermissionLevel.LOCAL_READ,
            handler=remember,
        ),
        Tool(
            name="search_memory",
            description=(
                "Search remembered facts and the user's indexed documents. Relevant memory is already "
                "attached to each message automatically; use this to look for something specific."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.LOCAL_READ,
            handler=search_memory,
        ),
        Tool(
            name="forget",
            description="Delete a remembered fact by its memory number. The user must approve.",
            input_schema={
                "type": "object",
                "properties": {"memory_id": {"type": "integer"}},
                "required": ["memory_id"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.LOCAL_WRITE,
            handler=forget,
        ),
        Tool(
            name="index_file",
            description=(
                "Add a file (PDF, text, Markdown or code) to searchable memory so its contents can be "
                "recalled later. Re-indexing an unchanged file does nothing."
            ),
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.LOCAL_READ,
            handler=index_file,
        ),
    ]
