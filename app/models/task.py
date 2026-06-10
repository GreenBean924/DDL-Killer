from datetime import datetime, timezone

from sqlalchemy import String, Text
from sqlalchemy import Float
from sqlalchemy import DateTime

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.database import Base


class Task(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    chatid: Mapped[str] = mapped_column(
        String(128),
        default="",
    )

    title: Mapped[str] = mapped_column(
        String(255)
    )

    short_code: Mapped[str] = mapped_column(
        String(16),
        default="",
        index=True,
    )

    difficulty: Mapped[float] = mapped_column(
        Float,
        default=5
    )

    importance: Mapped[float] = mapped_column(
        Float,
        default=5
    )

    risk_score: Mapped[float] = mapped_column(
        Float,
        default=0
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="pending"
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    ddl_time: Mapped[datetime] = mapped_column(
        DateTime
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )