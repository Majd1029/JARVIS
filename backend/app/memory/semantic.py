"""Semantic memory: documents split into chunks and embedded, for retrieval (RAG)."""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import DocumentChunkRow, DocumentRow
from app.memory.embeddings import Embedder
from app.memory.vectors import nearest
from app.tools.base import ToolError

CHUNK_CHARS = 1200   # ~300 tokens: small enough to inject a few into an 8K-context model
CHUNK_OVERLAP = 200  # characters repeated between chunks so a sentence isn't cut off from its context
MAX_CHUNKS = 400     # ~480K characters per document
EMBED_BATCH = 32
CHUNKER_VERSION = f"2:{CHUNK_CHARS}:{CHUNK_OVERLAP}"  # bump when chunk_text changes

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml", ".toml", ".ini",
                 ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sql", ".sh", ".ps1", ".java",
                 ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb", ".php", ".log"}


@dataclass
class Document:
    id: int
    path: str
    chunk_count: int
    updated_at: datetime


@dataclass
class Chunk:
    document_path: str
    chunk_index: int
    content: str
    score: float


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        try:
            pages = [page.extract_text() or "" for page in PdfReader(path).pages]
        except Exception as e:
            raise ToolError(f"Could not read PDF {path.name}: {e}") from e
        text = "\n\n".join(pages)
        if not text.strip():
            raise ToolError(f"{path.name} has no extractable text (it may be a scanned image).")
        return text
    if suffix not in TEXT_SUFFIXES:
        raise ToolError(f"Can't index {suffix or 'extension-less'} files. Supported: PDF and text/code files.")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ToolError(f"{path.name} is not a UTF-8 text file.") from e


def chunk_text(text: str) -> list[str]:
    """Pack paragraphs into ~CHUNK_CHARS chunks; split oversized paragraphs with overlap."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= CHUNK_CHARS:
            pieces.append(paragraph)
        else:
            pieces.extend(_split_long(paragraph))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > CHUNK_CHARS:
            chunks.append(current)
            current = _overlap_tail(current) + "\n\n" + piece if CHUNK_OVERLAP else piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def _overlap_tail(text: str) -> str:
    """The last ~CHUNK_OVERLAP characters, starting at a word boundary."""
    if len(text) <= CHUNK_OVERLAP:
        return text
    tail = text[-CHUNK_OVERLAP:]
    boundary = re.search(r"\s", tail)
    return tail[boundary.end():] if boundary else tail


def _split_long(paragraph: str) -> list[str]:
    """Split a paragraph longer than CHUNK_CHARS into overlapping windows, cutting at spaces."""
    pieces = []
    start = 0
    while start < len(paragraph):
        end = min(start + CHUNK_CHARS, len(paragraph))
        if end < len(paragraph):
            space = paragraph.rfind(" ", start + CHUNK_CHARS // 2, end)
            end = space if space != -1 else end
        pieces.append(paragraph[start:end].strip())
        if end >= len(paragraph):
            break
        next_start = max(end - CHUNK_OVERLAP, start + 1)
        space = paragraph.find(" ", next_start, end)
        start = space + 1 if space != -1 else next_start
    return pieces


class DocumentStore:
    def __init__(self, session_factory: sessionmaker[Session], embedder: Embedder):
        self._sessions = session_factory
        self.embedder = embedder

    def index(self, path: Path) -> tuple[Document, str]:
        """Index (or re-index) a file. Returns the document and 'indexed' / 'updated' / 'unchanged'."""
        text = extract_text(path)
        # The chunking settings are part of the hash, so changing them re-indexes unchanged files.
        content_hash = hashlib.sha256(f"{CHUNKER_VERSION}:{text}".encode()).hexdigest()
        key = str(path)

        with self._sessions() as session:
            existing = session.scalar(select(DocumentRow).where(DocumentRow.path == key))
            if existing and existing.content_hash == content_hash:
                return _doc(existing), "unchanged"

        chunks = chunk_text(text)
        if not chunks:
            raise ToolError(f"{path.name} is empty.")
        if len(chunks) > MAX_CHUNKS:
            raise ToolError(f"{path.name} is too large to index ({len(chunks)} chunks, limit {MAX_CHUNKS}).")
        embeddings: list[list[float]] = []
        for i in range(0, len(chunks), EMBED_BATCH):
            embeddings.extend(self.embedder.embed_documents(chunks[i:i + EMBED_BATCH]))

        with self._sessions.begin() as session:
            row = session.scalar(select(DocumentRow).where(DocumentRow.path == key))
            status = "updated" if row else "indexed"
            if row:
                session.execute(delete(DocumentChunkRow).where(DocumentChunkRow.document_id == row.id))
                row.content_hash, row.chunk_count, row.updated_at = content_hash, len(chunks), func.now()
            else:
                row = DocumentRow(path=key, content_hash=content_hash, chunk_count=len(chunks))
                session.add(row)
            session.flush()
            session.add_all(
                DocumentChunkRow(document_id=row.id, chunk_index=i, content=c, embedding=e)
                for i, (c, e) in enumerate(zip(chunks, embeddings))
            )
            session.flush()
            session.refresh(row)
            return _doc(row), status

    def all(self) -> list[Document]:
        with self._sessions() as session:
            return [_doc(r) for r in session.scalars(select(DocumentRow).order_by(DocumentRow.path))]

    def delete(self, document_id: int) -> bool:
        with self._sessions.begin() as session:
            session.execute(delete(DocumentChunkRow).where(DocumentChunkRow.document_id == document_id))
            return session.execute(delete(DocumentRow).where(DocumentRow.id == document_id)).rowcount > 0

    def search(self, query_embedding: list[float], limit: int, min_score: float) -> list[Chunk]:
        with self._sessions() as session:
            base = select(DocumentChunkRow).join(DocumentRow)
            results = nearest(session, DocumentChunkRow, query_embedding, limit, base=base)
            paths = {d.id: d.path for d in session.scalars(
                select(DocumentRow).where(DocumentRow.id.in_({r.document_id for r, _ in results}))
            )}
            return [
                Chunk(paths[row.document_id], row.chunk_index, row.content, score)
                for row, score in results
                if score >= min_score
            ]


def _doc(row: DocumentRow) -> Document:
    return Document(row.id, row.path, row.chunk_count, row.updated_at)
