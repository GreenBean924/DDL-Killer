"""MemoryFragment — vector-backed agent memory for DDL-Killer.

Stores embeddings of tasks and conversations for semantic retrieval.
Uses pgvector's Vector type for the embedding column.
"""

from datetime import datetime, timezone

from sqlalchemy import String, Text, Integer, DateTime, Column
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base

# pgvector Vector type — use Column (not Mapped) for compatibility
# since Vector is a TypeDecorator that predates SQLAlchemy 2.0 Mapped
try:
    from pgvector.sqlalchemy import Vector
    HAS_PGVECTOR = True
except ImportError:
    HAS_PGVECTOR = False


class MemoryFragment(Base):
    __tablename__ = "memory_fragment"

    id: Mapped[int] = mapped_column(primary_key=True)

    chatid: Mapped[str] = mapped_column(
        String(128),
        default="",
        index=True,
    )

    content: Mapped[str] = mapped_column(Text)

    # Vector column uses old-style Column for pgvector compatibility.
    # Dimension 512 for BAAI/bge-small-zh-v1.5; nullable for graceful degradation
    # when embedding generation fails.
    embedding = (
        Column(Vector(512), nullable=True)
        if HAS_PGVECTOR
        else Column(Text, nullable=True)  # fallback: store as text
    )

    memory_type: Mapped[str] = mapped_column(
        String(20),
        default="task",
    )

    source_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
