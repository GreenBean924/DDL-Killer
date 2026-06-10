"""ConversationRecord — persistent multi-turn conversation state."""

from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class ConversationRecord(Base):
    __tablename__ = "conversation"

    id: Mapped[int] = mapped_column(primary_key=True)

    chatid: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        default="",
    )

    intent: Mapped[str] = mapped_column(
        String(50),
        default="",
    )

    context_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
