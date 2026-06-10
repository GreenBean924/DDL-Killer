"""Enterprise WeChat smart bot callback handler.

GET  /api/bot/callback — URL verification
POST /api/bot/callback — message receive → parse → execute → reply
"""

import json
import os
import re

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import PlainTextResponse

from sqlalchemy.orm import Session

from app.database.database import get_db

from app.models.task import Task

from app.schemas.task import TaskCreate

from app.services.risk_score import calculate_risk
from app.services.wxcrypt import WXBizMsgCrypt

router = APIRouter(prefix="/api/bot")

WECOM_TOKEN = os.getenv("WECOM_TOKEN", "")
WECOM_ENCODING_AES_KEY = os.getenv("WECOM_ENCODING_AES_KEY", "")
WECOM_CORP_ID = os.getenv("WECOM_CORP_ID", "")

HELP_TEXT = """📋 **DDL-Killer 使用指南**

• **创建 标题 @YYYY-MM-DD HH:MM 难度7 重要度8**
  例：创建 数学作业 @2026-06-15 23:59 难度7 重要度9

• **列表** — 查看所有待办任务（按紧急度排序）

• **完成 1** — 将任务 1 标记为已完成

• **帮助** — 显示本指南"""


def _get_wxcpt() -> WXBizMsgCrypt:
    return WXBizMsgCrypt(WECOM_TOKEN, WECOM_ENCODING_AES_KEY, WECOM_CORP_ID)


def parse_create_command(text: str) -> dict | None:
    """Parse: 创建 标题 @YYYY-MM-DD HH:MM 难度N 重要度N"""
    pattern = r"^创建\s+(.+?)\s*@(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*难度(\d+)\s*重要度(\d+)\s*$"

    m = re.match(pattern, text.strip())
    if not m:
        return None

    from datetime import datetime

    return {
        "title": m.group(1).strip(),
        "ddl_time": datetime.strptime(m.group(2), "%Y-%m-%d %H:%M"),
        "difficulty": float(m.group(3)),
        "importance": float(m.group(4)),
    }


def parse_complete_command(text: str) -> int | None:
    """Parse: 完成 {id}"""
    m = re.match(r"^完成\s+(\d+)\s*$", text.strip())
    if m:
        return int(m.group(1))
    return None


def handle_message(content: str, db: Session) -> str:
    """Process a text message and return a reply string."""
    text = content.strip()

    # Command: list tasks
    if text in ("列表", "任务"):
        tasks = (
            db.query(Task)
            .filter(Task.status == "pending")
            .order_by(Task.risk_score.desc())
            .all()
        )

        if not tasks:
            return "暂无待办任务 🎉"

        lines = ["**📋 待办任务**\n"]
        for t in tasks:
            ddl = t.ddl_time.strftime("%m-%d %H:%M") if t.ddl_time else "无"
            lines.append(
                f"{t.id}. {t.title} | ⭐{t.importance:.0f} 📚{t.difficulty:.0f} "
                f"🔥{t.risk_score:.0f} | ⏰{ddl}"
            )
        return "\n".join(lines)

    # Command: complete task
    task_id = parse_complete_command(text)
    if task_id is not None:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return f"任务 {task_id} 不存在"
        task.status = "completed"
        db.commit()
        return f"✅ 任务 {task_id}「{task.title}」已标记完成"

    # Command: create task
    parsed = parse_create_command(text)
    if parsed:
        risk = calculate_risk(
            parsed["difficulty"],
            parsed["importance"],
            parsed["ddl_time"],
        )
        task = Task(
            title=parsed["title"],
            difficulty=parsed["difficulty"],
            importance=parsed["importance"],
            ddl_time=parsed["ddl_time"],
            risk_score=risk,
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        ddl_str = parsed["ddl_time"].strftime("%m-%d %H:%M")
        return (
            f"✅ 任务已创建\n"
            f"ID: {task.id}\n"
            f"标题: {task.title}\n"
            f"DDL: {ddl_str}\n"
            f"风险评分: {task.risk_score}"
        )

    # Command: help / unknown
    return HELP_TEXT


@router.get("/callback")
def verify_url(
    msg_signature: str = Query(alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """Handle GET request from WeChat Work for URL verification."""
    wxcpt = _get_wxcpt()
    ret, result = wxcpt.verify_url(msg_signature, timestamp, nonce, echostr)

    if ret != 0:
        raise HTTPException(status_code=403, detail=f"Verification failed: {result}")

    return PlainTextResponse(result)


@router.post("/callback")
async def receive_message(
    request: Request,
    msg_signature: str = Query(alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    db: Session = Depends(get_db),
):
    """Handle POST request from WeChat Work with encrypted message."""
    body = await request.json()
    encrypted = body.get("encrypt", "")

    wxcpt = _get_wxcpt()
    ret, decrypted = wxcpt.decrypt_msg(msg_signature, timestamp, nonce, encrypted)

    if ret != 0:
        raise HTTPException(status_code=403, detail=f"Decrypt failed: {decrypted}")

    msg = json.loads(decrypted)

    # Extract text content from smart bot message format
    content = ""
    msg_type = msg.get("msgtype", "text")

    if msg_type == "text":
        content = msg.get("text", {}).get("content", "")
    elif msg_type == "event":
        # Handle events (e.g., user enters chat)
        return PlainTextResponse("")

    if not content:
        return PlainTextResponse("")

    # Process the message
    reply = handle_message(content, db)

    # Encrypt the reply
    encrypted_reply = wxcpt.encrypt_msg(reply, nonce)

    return PlainTextResponse(encrypted_reply)
