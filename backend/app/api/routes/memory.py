"""Memory endpoints: review, add and delete facts; index, list and remove documents; search."""

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.memory.embeddings import EmbeddingError
from app.memory.retrieval import Retriever
from app.tools.base import ToolError
from app.tools.filesystem import Sandbox

router = APIRouter(prefix="/memory", tags=["memory"])


class FactIn(BaseModel):
    content: str = Field(min_length=1)


class FactOut(BaseModel):
    id: int
    content: str
    created_at: datetime
    score: float | None = None


class DocumentIn(BaseModel):
    path: str = Field(min_length=1, description="A file inside the allowed folders.")


class DocumentOut(BaseModel):
    id: int
    path: str
    chunk_count: int
    updated_at: datetime


class IndexOut(BaseModel):
    status: str  # indexed | updated | unchanged
    document: DocumentOut


class ChunkOut(BaseModel):
    document_path: str
    chunk_index: int
    content: str
    score: float


class SearchOut(BaseModel):
    facts: list[FactOut]
    chunks: list[ChunkOut]


def _retriever(request: Request) -> Retriever:
    return request.app.state.retriever


@contextmanager
def _memory_errors() -> Iterator[None]:
    try:
        yield
    except EmbeddingError as e:
        raise HTTPException(503, f"Embedding model unavailable: {e}")
    except ToolError as e:
        raise HTTPException(400, str(e))


@router.get("/facts", response_model=list[FactOut])
def list_facts(request: Request) -> list[FactOut]:
    return [FactOut(**vars(f)) for f in _retriever(request).facts.all()]


@router.post("/facts", response_model=FactOut, status_code=201)
def add_fact(body: FactIn, request: Request) -> FactOut:
    with _memory_errors():
        fact, _ = _retriever(request).facts.add(body.content)
    return FactOut(**vars(fact))


@router.delete("/facts/{fact_id}", status_code=204)
def delete_fact(fact_id: int, request: Request) -> None:
    if not _retriever(request).facts.delete(fact_id):
        raise HTTPException(404, f"No memory #{fact_id}")


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(request: Request) -> list[DocumentOut]:
    return [DocumentOut(**vars(d)) for d in _retriever(request).documents.all()]


@router.post("/documents", response_model=IndexOut)
def index_document(body: DocumentIn, request: Request) -> IndexOut:
    sandbox: Sandbox = request.app.state.sandbox
    with _memory_errors():
        document, status = _retriever(request).documents.index(sandbox.resolve(body.path))
    return IndexOut(status=status, document=DocumentOut(**vars(document)))


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: int, request: Request) -> None:
    if not _retriever(request).documents.delete(document_id):
        raise HTTPException(404, f"No document #{document_id}")


@router.get("/search", response_model=SearchOut)
def search(q: str, request: Request) -> SearchOut:
    with _memory_errors():
        found = _retriever(request).recall(q, fact_limit=10, chunk_limit=5)
    return SearchOut(facts=[FactOut(**vars(f)) for f in found.facts],
                     chunks=[ChunkOut(**vars(c)) for c in found.chunks])
