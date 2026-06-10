"""TaskFile — file attachments associated with tasks."""

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class TaskFile(Base):
    __tablename__ = "task_file"

    id: Mapped[int] = mapped_column(primary_key=True)

    task_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("task.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

    chatid: Mapped[str] = mapped_column(
        String(128),
        default="",
    )

    original_name: Mapped[str] = mapped_column(
        String(512),
        default="",
    )

    label: Mapped[str] = mapped_column(
        String(128),
        default="",
    )

    stored_path: Mapped[str] = mapped_column(
        String(1024),
        default="",
    )

    file_type: Mapped[str] = mapped_column(
        String(20),
        default="file",
    )

    size: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
