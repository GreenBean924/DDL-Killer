from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.models.task import Task
from app.schemas.task import TaskCreate, TaskUpdate, TaskResponse
from app.services.risk_score import calculate_risk

router = APIRouter(prefix="/tasks")


@router.post("/", response_model=TaskResponse)
def create_task(task: TaskCreate, db: Session = Depends(get_db)):
    risk_score = calculate_risk(task.difficulty, task.importance, task.ddl_time)

    db_task = Task(
        chatid=task.chatid,
        title=task.title,
        difficulty=task.difficulty,
        importance=task.importance,
        ddl_time=task.ddl_time,
        risk_score=risk_score,
    )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


@router.get("/", response_model=list[TaskResponse])
def get_tasks(
    status: str | None = Query(default=None),
    sort_by: str | None = Query(default=None),
    chatid: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(Task)

    if chatid:
        query = query.filter(Task.chatid == chatid)

    if status:
        query = query.filter(Task.status == status)

    if sort_by == "ddl_time":
        query = query.order_by(Task.ddl_time.asc())
    else:
        query = query.order_by(Task.risk_score.desc())

    return query.all()


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/{task_id}", response_model=TaskResponse)
def update_task(task_id: int, update: TaskUpdate, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(task, field, value)

    risk_fields = {"difficulty", "importance", "ddl_time"}
    if risk_fields & set(update_data.keys()):
        task.risk_score = calculate_risk(task.difficulty, task.importance, task.ddl_time)

    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(task)
    db.commit()
