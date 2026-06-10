"""LLM-powered natural language understanding for DDL-Killer bot.

Uses DeepSeek API (OpenAI-compatible) with tool calling to:
- Classify user intent (create / query / complete / chat / list_files)
- Extract structured fields from free-form messages
- Decide when to ask follow-up questions for missing info
"""

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.database.database import SessionLocal
from app.models.task import Task
from app.models.task_file import TaskFile
from app.services.risk_score import calculate_risk
from app.services.display import (
    file_display_name,
    format_task_list,
    format_task_list_with_completed,
    format_task_created,
    format_file_list_by_task,
    format_file_panel,
    format_files_sending,
)

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

LLM_TIMEOUT = 8.0

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMResult:
    intent: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    reply: str = ""
    finish_reason: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# Display helpers are imported from app.services.display


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _build_system_prompt(memories: list[str] | None = None) -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today_str = now.strftime("%Y-%m-%d %H:%M")
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    base = (
        f"你是 DDL-Killer，一个大学生任务管理助手。简洁、口语化，像同学聊天一样。\n\n"
        f"今天是 {today_str}，{weekday}。\n\n"
        "你需要识别用户的意图：\n"
        "1. 创建任务 — 从消息中提取：任务标题、DDL时间、难度(1-10)、重要度(1-10)、描述(可选)\n"
        "   - 标题和DDL时间必须确定；如果都清楚了，直接调用 create_task\n"
        "   - 难度和重要度：用户没说就用默认值5，不要追问。只有用户自己提了才映射为数值：\n"
        "     难度：「很难」「超难」「巨难」=9，「有点难」=7，「还行」「一般」=5，「简单」「轻松」=2-3\n"
        "     重要度：「非常重要」「必须搞定」=9，「挺重要」=7，「无所谓」「不太重要」=3，「随便」=2\n"
        "   - 只追问标题或DDL时间不清楚的情况，一次只问一个最关键的问题\n"
        "   - 中文日期推算规则（必须严格遵守）：\n"
        "     「今天」= 当天 23:59，「明天」= 明天 23:59，「后天」= 后天 23:59，「大后天」= +3天\n"
        "     「下周X」= 下周的周X（如果今天是周一，「下周一」= 8天后），「这周X」= 本周的周X\n"
        "     「月底」= 当月最后一天 23:59，「下个月底」= 下月最后一天\n"
        "     「N天后」= 当前日期+N天，「N周后」= 当前日期+N*7天\n"
        "     如果用户只说了日期没说时间，默认 23:59\n"
        "   - 如果用户提供了任务描述/备注/详情，填入 description 字段\n"
        "2. 查询任务 — 用户想看自己的待办 DDL\n"
        "   - 调用 query_tasks 获取列表\n"
        "3. 完成任务 — 用户说某个任务做完了\n"
        "   - 如果用户说了编码（如 #DB-LAB4）或数字ID，用 task_id；如果说了名字，用 title_hint 模糊匹配\n"
        "   - title_hint 也支持短编码匹配（如用户说「完成 DB-LAB4」，填入 title_hint: \"DB-LAB4\"）\n"
        "   - 用户说「都做完了」「全部完成」「没有待办了」→ 不要调用 complete_task（你没有所有任务的ID），让用户回复「列表」确认后逐个完成\n"
        "4. 查看文件/附件 — 用户想看任务的文件\n"
        "   - 有任务ID/名字 → 传 task_id\n"
        "   - 用户说「我的文件」「有哪些附件」→ 不传 task_id，列出所有\n"
        "5. 发送文件 — 用户要求发送/传输某个任务的附件\n"
        "   - 「论文文件发我」「把UML的附件传过来」→ 调用 send_files\n"
        "   - 有任务ID用 task_id，否则用 title_hint 模糊匹配\n"
        "6. 关联文件 — 用户想把最近收到的文件绑定到某个任务\n"
        "   - 「把这个文件关联到数据库实验」「关联到JavaWeb」「文件绑定到那个报告」→ 调用 associate_file\n"
        "   - 用户只说任务名且有待关联文件时，调用 associate_file\n"
        "7. 闲聊 — 打招呼、感谢、无关话题，简短回复即可，不要调用 tool\n\n"
        "核心原则：\n"
        "- 能用默认值就不要追问，让用户少打字\n"
        "- 用自然的口语回复，不要说「你可以…」开头\n"
        "- 当无法确定用户意图时，给出1-2个具体的下一步建议（如「回复编号标记完成」），而不是笼统的「想做什么？」\n"
        "- 每次只回 1-2 句话"
    )
    if memories:
        base += (
            "\n\n相关历史记忆（参考这些来理解用户说的「上次」「之前那个」等指代，"
            "用记忆中的信息帮助消歧）：\n" + "\n".join(f"- {m}" for m in memories)
        )
    return base


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "创建一个新任务。只有标题、DDL时间、难度、重要度四个字段都确定时才调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "任务标题/名称，简洁概括",
                    },
                    "ddl_time": {
                        "type": "string",
                        "description": "DDL 截止时间，ISO8601 格式如 2026-06-15T17:00:00",
                    },
                    "difficulty": {
                        "type": "number",
                        "description": "难度评估 1-10，1最简单，10最难",
                    },
                    "importance": {
                        "type": "number",
                        "description": "重要度评估 1-10，1最不重要，10最重要",
                    },
                    "description": {
                        "type": "string",
                        "description": "任务描述/备注/要求，可选",
                    },
                },
                "required": ["title", "ddl_time", "difficulty", "importance"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_tasks",
            "description": "用户想查看自己的待办任务/DDL列表，或询问「还有什么没做完」等",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "用户标记一个任务为已完成。如果用户说了任务ID，用 task_id；如果说了名字，用 title_hint",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "任务ID，用户明确提到时填写",
                    },
                    "title_hint": {
                        "type": "string",
                        "description": "任务名称关键词，用户没说ID但说了任务名时填写",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "用户想看某个任务的附件/文件，或查看所有文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "任务ID，用户想查看哪个任务的文件。留空表示列出所有文件",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_files",
            "description": "用户要求发送/传输某个任务的附件文件。如「把论文文件发我」「传一下UML的附件」",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "任务ID，用户明确提到时填写",
                    },
                    "title_hint": {
                        "type": "string",
                        "description": "任务名称关键词，用户没说ID但说了任务名时填写",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "associate_file",
            "description": "用户想把最近收到的文件关联到某个任务。如「把这个文件关联到数据库实验」「关联到JavaWeb」「文件绑定到那个报告」",
            "parameters": {
                "type": "object",
                "properties": {
                    "title_hint": {
                        "type": "string",
                        "description": "任务名称关键词或短编码，用于定位要关联的任务",
                    },
                },
                "required": ["title_hint"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Core LLM call
# ---------------------------------------------------------------------------


async def llm_parse_message(
    user_message: str,
    conversation_history: Optional[list[dict]],
    chatid: str = "",
    memories: list[str] | None = None,
) -> LLMResult:
    if not DEEPSEEK_API_KEY:
        return _fallback("LLM API key 未配置")

    client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    messages = [{"role": "system", "content": _build_system_prompt(memories)}]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.1,
            ),
            timeout=LLM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return _fallback("LLM 响应超时，请重试")
    except Exception as e:
        err_msg = str(e)
        if DEEPSEEK_API_KEY in err_msg:
            err_msg = err_msg.replace(DEEPSEEK_API_KEY, "***")
        print(f"[LLM] API error: {err_msg}")
        return _fallback(f"LLM 调用失败：{err_msg[:60]}")

    choice = response.choices[0]
    finish_reason = choice.finish_reason or "stop"
    reply = choice.message.content or ""
    tool_calls = []

    intent = "chat"
    if choice.message.tool_calls:
        for tc in choice.message.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(name=tc.function.name, arguments=args))
        if tool_calls:
            intent = tool_calls[0].name

    print(f"[LLM] intent={intent} finish={finish_reason} reply_preview={reply[:50] if reply else 'None'}")
    return LLMResult(
        intent=intent,
        tool_calls=tool_calls,
        reply=reply,
        finish_reason=finish_reason,
    )


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def execute_query_tasks(chatid: str, db: Session) -> str:
    pending = (
        db.query(Task)
        .filter(Task.chatid == chatid, Task.status == "pending")
        .order_by(Task.risk_score.desc())
        .all()
    )
    completed = (
        db.query(Task)
        .filter(Task.chatid == chatid, Task.status == "completed")
        .order_by(Task.id.desc())
        .limit(10)
        .all()
    )
    return format_task_list_with_completed(pending, completed)


def execute_associate_file(chatid: str, args: dict, db: Session) -> str:
    """Associate the latest unassociated file with a task by title_hint."""
    from app.services.file_manager import get_latest_unassociated, associate_latest_file

    title_hint = args.get("title_hint", "")

    latest = get_latest_unassociated(chatid)
    if not latest:
        return "没有待关联的文件"

    # Exact match: short_code or title
    task = (
        db.query(Task)
        .filter(Task.chatid == chatid, Task.status == "pending", Task.short_code == title_hint.upper())
        .first()
    )
    if not task:
        task = (
            db.query(Task)
            .filter(Task.chatid == chatid, Task.status == "pending", Task.title == title_hint)
            .first()
        )
    if not task:
        # Fuzzy match
        task = (
            db.query(Task)
            .filter(Task.chatid == chatid, Task.status == "pending", Task.title.ilike(f"%{title_hint}%"))
            .order_by(Task.risk_score.desc())
            .first()
        )

    if not task:
        return f"找不到匹配「{title_hint}」的任务"

    return associate_latest_file(chatid, task.id)


def execute_create_task(chatid: str, args: dict, db: Session) -> str:
    from datetime import datetime as dt
    from app.services.short_code import generate_short_code_sync

    title = args.get("title", "未命名任务")
    ddl_str = args.get("ddl_time", "")
    difficulty = float(args.get("difficulty", 5))
    importance = float(args.get("importance", 5))
    description = args.get("description") or None

    try:
        ddl_time = dt.fromisoformat(ddl_str)
        if ddl_time.tzinfo is not None:
            ddl_time = ddl_time.replace(tzinfo=None)
    except (ValueError, TypeError):
        ddl_time = dt.now().replace(hour=23, minute=59)

    risk = calculate_risk(difficulty, importance, ddl_time)

    # Generate short code
    existing = {r[0] for r in db.query(Task.short_code).filter(Task.short_code != "").all()}
    code = generate_short_code_sync(title, existing)

    task = Task(
        chatid=chatid,
        title=title,
        short_code=code,
        difficulty=min(max(difficulty, 1), 10),
        importance=min(max(importance, 1), 10),
        ddl_time=ddl_time,
        risk_score=risk,
        description=description,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    return format_task_created(task)


def execute_complete_task(chatid: str, args: dict, db: Session) -> str:
    from app.services.file_manager import delete_files_for_tasks

    task_id = args.get("task_id")
    title_hint = args.get("title_hint", "")

    if task_id:
        task = (
            db.query(Task)
            .filter(Task.id == task_id, Task.chatid == chatid)
            .first()
        )
        if not task:
            return f"任务 {task_id} 不存在"
        task.status = "completed"
        db.commit()
        file_count = delete_files_for_tasks(chatid, [task.id])
        code = f"#{task.short_code}" if task.short_code else str(task.id)
        reply = f"任务 {code}「{task.title}」已完成 ✅"
        if file_count > 0:
            reply += f"\n已清理 {file_count} 个附件"
        return reply

    if title_hint:
        # Try short_code exact match first
        task = (
            db.query(Task)
            .filter(
                Task.chatid == chatid,
                Task.status == "pending",
                Task.short_code == title_hint.upper(),
            )
            .first()
        )
        if task:
            task.status = "completed"
            db.commit()
            file_count = delete_files_for_tasks(chatid, [task.id])
            code = f"#{task.short_code}" if task.short_code else str(task.id)
            reply = f"任务 {code}「{task.title}」已完成 ✅"
            if file_count > 0:
                reply += f"\n已清理 {file_count} 个附件"
            return reply

        # Fall back to title fuzzy match
        tasks = (
            db.query(Task)
            .filter(
                Task.chatid == chatid,
                Task.status == "pending",
                Task.title.ilike(f"%{title_hint}%"),
            )
            .order_by(Task.risk_score.desc())
            .all()
        )
        if not tasks:
            return f"没找到包含「{title_hint}」的待办任务"
        if len(tasks) == 1:
            t = tasks[0]
            t.status = "completed"
            db.commit()
            file_count = delete_files_for_tasks(chatid, [t.id])
            code = f"#{t.short_code}" if t.short_code else str(t.id)
            reply = f"任务 {code}「{t.title}」已完成 ✅"
            if file_count > 0:
                reply += f"\n已清理 {file_count} 个附件"
            return reply
        return "找到以下匹配任务，请回复编号：\n" + format_task_list(tasks)

    return "不确定你想完成哪个任务，说「完成 编号」或告诉我任务名字"


def execute_list_files(chatid: str, args: dict, db: Session) -> str:
    task_id = args.get("task_id")

    if task_id:
        files = (
            db.query(TaskFile)
            .filter(TaskFile.chatid == chatid, TaskFile.task_id == task_id)
            .order_by(TaskFile.created_at.desc())
            .all()
        )
        task = db.query(Task).filter(Task.id == task_id).first()
        task_name = task.title if task else f"任务{task_id}"
        return format_file_list_by_task(files, task_id, task_name)

    # No task_id → list all files grouped
    from app.services.file_manager import list_all_files
    grouped = list_all_files(chatid)
    return format_file_panel(grouped)


def execute_send_files(chatid: str, args: dict, db: Session) -> tuple[str, list[dict]]:
    """Find files matching task_id or title_hint. Returns (notice, file_info_list).

    Does NOT send files — the caller (bot_ws_client.py) handles that via SDK.
    """
    task_id = args.get("task_id")
    title_hint = args.get("title_hint", "")

    task = None
    if task_id:
        task = db.query(Task).filter(Task.id == task_id, Task.chatid == chatid).first()
    elif title_hint:
        tasks = (
            db.query(Task)
            .filter(
                Task.chatid == chatid,
                Task.status == "pending",
                Task.title.ilike(f"%{title_hint}%"),
            )
            .order_by(Task.risk_score.desc())
            .all()
        )
        if len(tasks) == 1:
            task = tasks[0]
        elif len(tasks) > 1:
            return (
                "找到以下匹配任务，请回复编号：\n" + format_task_list(tasks),
                [],
            )
        else:
            return (f"没找到包含「{title_hint}」的任务", [])

    if task is None:
        return ("找不到对应的任务", [])

    files = (
        db.query(TaskFile)
        .filter(TaskFile.chatid == chatid, TaskFile.task_id == task.id)
        .order_by(TaskFile.created_at.desc())
        .all()
    )

    if not files:
        return (f"任务「{task.title}」暂无附件", [])

    file_info_list = [
        {"stored_path": f.stored_path, "label": file_display_name(f), "file_type": f.file_type}
        for f in files
    ]
    notice = format_files_sending(task.title, task.id, files)
    return (notice, file_info_list)


# ---------------------------------------------------------------------------
# Dispatch all tool calls
# ---------------------------------------------------------------------------


def execute_tool_calls(result: LLMResult, chatid: str) -> str:
    replies = []

    for tc in result.tool_calls:
        if tc.name == "query_tasks":
            db = SessionLocal()
            try:
                replies.append(execute_query_tasks(chatid, db))
            finally:
                db.close()

        elif tc.name == "create_task":
            db = SessionLocal()
            try:
                replies.append(execute_create_task(chatid, tc.arguments, db))
            finally:
                db.close()

        elif tc.name == "complete_task":
            db = SessionLocal()
            try:
                replies.append(execute_complete_task(chatid, tc.arguments, db))
            finally:
                db.close()

        elif tc.name == "list_files":
            db = SessionLocal()
            try:
                replies.append(execute_list_files(chatid, tc.arguments, db))
            finally:
                db.close()

        elif tc.name == "send_files":
            # send_files is handled in bot_ws_client.py (needs frame + async)
            pass

        elif tc.name == "associate_file":
            db = SessionLocal()
            try:
                replies.append(execute_associate_file(chatid, tc.arguments, db))
            finally:
                db.close()

    return "\n\n".join(replies) if replies else ""


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


FALLBACK_TEXT = (
    "LLM 暂时不可用，请用固定格式：\n"
    "`创建 任务名 @YYYY-MM-DD HH:MM 难度N 重要度N`\n"
    "`列表` 查看 · `完成 N` 标记 · `文件` 文件面板"
)


def _fallback(reason: str = "") -> LLMResult:
    return LLMResult(
        intent="chat",
        reply=f"({reason})\n{FALLBACK_TEXT}" if reason else FALLBACK_TEXT,
        finish_reason="stop",
    )
