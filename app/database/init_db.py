"""Initialize or migrate the database.

Use Alembic for all schema changes:

    alembic revision --autogenerate -m "description"
    alembic upgrade head

Import models here to ensure they register with Base.metadata before autogenerate runs.
"""

# Import models so they register with Base.metadata
from app.models.task import Task  # noqa: F401
from app.models.task_file import TaskFile  # noqa: F401
from app.models.conversation import ConversationRecord  # noqa: F401
from app.models.memory import MemoryFragment  # noqa: F401
