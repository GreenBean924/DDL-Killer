from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TaskCreate(BaseModel):
    title: str
    difficulty: float
    importance: float
    ddl_time: datetime
    chatid: str = ""


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    difficulty: Optional[float] = None
    importance: Optional[float] = None
    ddl_time: Optional[datetime] = None
    status: Optional[str] = None


class TaskResponse(BaseModel):
    id: int
    chatid: str
    title: str
    difficulty: float
    importance: float
    risk_score: float
    status: str
    ddl_time: datetime
    created_at: datetime

    class Config:
        from_attributes = True
