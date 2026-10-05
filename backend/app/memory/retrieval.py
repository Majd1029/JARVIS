"""Retrieval: find the facts and document excerpts relevant to a message.

Relevant memory is attached to each user message automatically (rather than relying on the
model to call a search tool), because small local models often don't think to look.
"""

from dataclasses import dataclass, field
from typing import Any

from app.memory.embeddings import Embedder
from app.memory.long_term import Fact, FactStore
from app.memory.semantic import Chunk, DocumentStore

MEMORY_TAG = "<memory>"  # marks the injected block so the readable history can hide it


@dataclass
class Recall:
    facts: list[Fact] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.facts or self.chunks)

    def summary(self) -> dict[str, Any]:
        return {
            "facts": [{"id": f.id, "score": round(f.score or 0, 3)} for f in self.facts],
            "chunks": [{"path": c.document_path, "index": c.chunk_index, "score": round(c.score, 3)}
                       for c in self.chunks],
        }


class Retriever:
    def __init__(self, embedder: Embedder, facts: FactStore, documents: DocumentStore,
                 min_score: float = 0.55, fact_limit: int = 5, chunk_limit: int = 3):
        self.embedder = embedder
        self.facts = facts
        self.documents = documents
        self.min_score = min_score
        self.fact_limit = fact_limit
        self.chunk_limit = chunk_limit

    def recall(self, query: str, fact_limit: int | None = None, chunk_limit: int | None = None) -> Recall:
        embedding = self.embedder.embed_query(query)
        return Recall(
            facts=self.facts.search(embedding, fact_limit or self.fact_limit, self.min_score),
            chunks=self.documents.search(embedding, chunk_limit or self.chunk_limit, self.min_score),
        )

    @staticmethod
    def context_block(recall: Recall) -> str:
        lines = [MEMORY_TAG,
                 "Retrieved automatically from JARVIS's memory. Use it if relevant; ignore it otherwise."]
        if recall.facts:
            lines.append("\nWhat you remember about the user:")
            lines += [f"- {f.content} (memory #{f.id})" for f in recall.facts]
        if recall.chunks:
            lines.append("\nExcerpts from the user's indexed documents:")
            for c in recall.chunks:
                lines.append(f"\n[{c.document_path}, part {c.chunk_index + 1}]\n{c.content}")
        lines.append("</memory>")
        return "\n".join(lines)
