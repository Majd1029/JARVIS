"""Database schema. Change it here, then generate a migration:  alembic revision --autogenerate -m "..." """

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.memory.embeddings import EMBEDDING_DIMS

# JSONB on PostgreSQL; plain JSON elsewhere (the tests run on SQLite). Python None -> SQL NULL.
JSONType = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
# Plain JSON keeps the exact text (JSONB reorders keys), so message history replays to Claude unchanged.
VerbatimJSON = JSON(none_as_null=True)
# BIGSERIAL on PostgreSQL; SQLite only auto-increments INTEGER primary keys.
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")
# pgvector on PostgreSQL; a JSON list elsewhere (similarity is then computed in Python).
EmbeddingType = Vector(EMBEDDING_DIMS).with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(200))
    # Tool calls waiting for the user's approval (null when nothing is pending).
    pending: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageRow(Base):
    """One entry of the Messages API history, stored exactly as sent/received."""

    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "seq"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)  # position within the conversation
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[Any] = mapped_column(VerbatimJSON)  # a string, or a list of content blocks
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryRow(Base):
    """A long-term fact JARVIS chose (or was asked) to remember."""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentRow(Base):
    """A file indexed into semantic memory."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(Text, unique=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentChunkRow(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingType)
