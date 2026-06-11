"""Enterprise WeChat smart bot — long-connection mode via official SDK.

Connects to WeChat Work WebSocket, subscribes, receives user messages,
processes commands (regex fast-path + LLM NLU), and replies.

Message types handled: text, image, file, event, stream.
"""

import asyncio
import re
import shutil
import tempfile
from pathlib import Path

from wecom_aibot_sdk import WSClient, WSClientOptions

from app.database.database import SessionLocal
from app.models.task import Task
from app.models.task_file import TaskFile
from app.services.risk_score import calculate_risk
from app.services.conversation import ConversationManager
from app.services.llm_service import llm_parse_message, execute_tool_calls, execute_send_files
from app.services.memory_service import get_memory_service
from app.services.file_manager import (
    download_and_save,
    list_task_files,
    list_all_files,
    get_recent_tasks,
    associate_file,
    associate_file_by_hint,
    list_completed_task_files,
    delete_files_for_tasks,
    BASE_UPLOAD_DIR,
)
from app.services.display import (
    format_task_list_with_completed,
    format_task_created,
    format_file_list_by_task,
    format_file_panel,
    format_file_received,
    format_files_sending,
    format_cleanup_confirm,
    format_cleanup_done,
)

# Module-level memory service singleton
memory_service = get_memory_service()

HELP_TEXT = """📚 DDL-Killer

创建任务 — 直接发消息
例：下周五交论文 难度7

命令
列表 — 查看待办
完成 N — 标记已完成
提醒 — 立即推送一条任务提醒
文件 — 文件面板
文件 N — 任务附件
关联 文件ID 任务ID
清理已完成 — 清理附件
帮助 — 显示本指南"""


# Display helpers are imported from app.services.display
#
# Regex parsers
# ---------------------------------------------------------------------------


def parse_create_command(text: str) -> dict | None:
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
    m = re.match(r"^完成\s+#?([A-Za-z0-9\-]+)\s*$", text.strip())
    if not m:
        return None
    ident = m.group(1)
    # Try numeric ID first
    try:
        return int(ident)
    except ValueError:
        pass
    # Return as short_code string
    return ident.upper()


def parse_file_list_command(text: str) -> int | None:
    """Parse: 文件 <task_id>"""
    m = re.match(r"^文件\s+(\d+)\s*$", text.strip())
    return int(m.group(1)) if m else None


def parse_associate_command(text: str) -> tuple[int, int] | None:
    """Parse: 关联 <file_id> <task_id>"""
    m = re.match(r"^关联\s+(\d+)\s+(\d+)\s*$", text.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def is_cleanup_command(text: str) -> bool:
    return text.strip() in ("清理已完成", "清理")


def is_cleanup_confirm(text: str) -> bool:
    return text.strip() == "确认清理"


# ---------------------------------------------------------------------------
# Regex-based operations
# ---------------------------------------------------------------------------


def handle_create_regex(parsed: dict, chatid: str) -> tuple[str, int]:
    """Create task via regex. Returns (reply_text, task_id)."""
    from app.services.short_code import generate_short_code_sync

    db = SessionLocal()
    try:
        # Get existing short codes for uniqueness check
        existing = {r[0] for r in db.query(Task.short_code).filter(Task.short_code != "").all()}
        code = generate_short_code_sync(parsed["title"], existing)

        risk = calculate_risk(parsed["difficulty"], parsed["importance"], parsed["ddl_time"])
        task = Task(
            chatid=chatid,
            title=parsed["title"],
            short_code=code,
            difficulty=parsed["difficulty"],
            importance=parsed["importance"],
            ddl_time=parsed["ddl_time"],
            risk_score=risk,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return format_task_created(task), task.id
    finally:
        db.close()


def handle_list_regex(chatid: str) -> str:
    db = SessionLocal()
    try:
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
    finally:
        db.close()


def handle_complete_regex(identifier: int | str, chatid: str) -> str:
    """Complete a task by numeric ID or short_code."""
    db = SessionLocal()
    try:
        if isinstance(identifier, int):
            task = db.query(Task).filter(Task.id == identifier, Task.chatid == chatid).first()
        else:
            task = db.query(Task).filter(
                Task.short_code == identifier, Task.chatid == chatid, Task.status == "pending"
            ).first()
        if not task:
            return f"任务 {identifier} 不存在"
        task.status = "completed"
        db.commit()
    finally:
        db.close()

    # Auto-delete associated files
    file_count = delete_files_for_tasks(chatid, [task.id])

    # Fire-and-forget memory store
    asyncio.create_task(
        memory_service.store(
            chatid,
            f"完成了任务「{task.title}」",
            "completion",
        )
    )

    code = f"#{task.short_code}" if task.short_code else str(task.id)
    reply = f"任务 {code}「{task.title}」已完成 ✅"
    if file_count > 0:
        reply += f"\n已清理 {file_count} 个附件"
    return reply


def handle_file_list_regex(task_id: int, chatid: str) -> str:
    files = list_task_files(chatid, task_id)
    if not files:
        return f"任务 {task_id} 暂无附件"
    # Fetch task name for the panel header
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        task_name = task.title if task else f"任务{task_id}"
    finally:
        db.close()
    return format_file_list_by_task(files, task_id, task_name)


def handle_file_all_regex(chatid: str) -> str:
    grouped = list_all_files(chatid)
    return format_file_panel(grouped)


def handle_associate_regex(file_id: int, task_id: int, chatid: str) -> str:
    result = associate_file(chatid, file_id, task_id)
    # Trigger auto-decomposition on successful file association
    if "已关联" in result:
        asyncio.create_task(_decompose_and_notify(chatid, task_id))
    return result


def handle_cleanup_list(chatid: str) -> str:
    """Show what will be deleted, ask for confirmation."""
    grouped = list_completed_task_files(chatid)
    return format_cleanup_confirm(grouped)


def handle_cleanup_execute(chatid: str) -> str:
    """Actually delete files for all completed tasks."""
    db = SessionLocal()
    try:
        completed_ids = [
            row[0]
            for row in db.query(Task.id)
            .filter(Task.chatid == chatid, Task.status == "completed")
            .all()
        ]
    finally:
        db.close()

    if not completed_ids:
        return "没有已完成的任务"

    count = delete_files_for_tasks(chatid, completed_ids)
    return format_cleanup_done(count)


# ---------------------------------------------------------------------------
# Conversation manager
# ---------------------------------------------------------------------------

conv_manager = ConversationManager()


# ---------------------------------------------------------------------------
# File message handler
# ---------------------------------------------------------------------------


# File queue: per-chatid FIFO of unassociated file IDs
_file_queue: dict[str, list[int]] = {}


async def handle_file_message(frame, chatid: str, file_type: str, metadata: dict) -> str:
    """Download, save, then ask user which task to associate with.

    Uses a per-chatid queue so that multiple files don't overwrite each other.
    """
    try:
        task_file = await download_and_save(_client, chatid, file_type, metadata)
    except Exception as e:
        print(f"[Bot] File download error: {e}")
        return f"文件保存失败：{e}"

    # Add to file queue (FIFO)
    _file_queue.setdefault(chatid, []).append(task_file.id)
    queue_len = len(_file_queue[chatid])

    tasks = get_recent_tasks(chatid, limit=5)
    reply = format_file_received(task_file, tasks, queue_pos=queue_len)

    # Don't set conversation state — user stays free to type naturally
    return reply


# ---------------------------------------------------------------------------
# Main message handler
# ---------------------------------------------------------------------------


async def handle_message(text: str, chatid: str, frame=None) -> str:
    text = text.strip()
    if not text:
        return ""

    # Check conversation first — user might be responding to a prompt
    conv = conv_manager.get(chatid)

    # ---- Natural file association: if user types just a task name/code ----
    # and there are unassociated files, try to associate the latest one.
    # This replaces the old conversation-based approach.
    if _file_queue.get(chatid) and not _is_command_or_task(text):
        # Try to match as task name/short_code
        from app.services.file_manager import get_latest_unassociated, associate_latest_file

        db_tmp = SessionLocal()
        try:
            # Exact match on short_code or title
            candidate = (
                db_tmp.query(Task)
                .filter(
                    Task.chatid == chatid,
                    Task.status == "pending",
                    Task.short_code == text.upper(),
                )
                .first()
            )
            if not candidate:
                candidate = (
                    db_tmp.query(Task)
                    .filter(Task.chatid == chatid, Task.status == "pending", Task.title == text)
                    .first()
                )
            if candidate:
                latest = get_latest_unassociated(chatid)
                if latest:
                    result = associate_latest_file(chatid, candidate.id)
                    # Pop from queue
                    if latest.id in _file_queue.get(chatid, []):
                        _file_queue[chatid].remove(latest.id)
                    if "已关联" in result:
                        asyncio.create_task(_decompose_and_notify(chatid, candidate.id))
                    return result
        finally:
            db_tmp.close()

    # ---- Handle cleanup confirmation ----
    if conv and conv.intent == "cleanup_confirm":
        if is_cleanup_confirm(text):
            conv_manager.delete(chatid)
            return handle_cleanup_execute(chatid)
        else:
            # User said something else — cancel cleanup
            conv_manager.delete(chatid)
            return "已取消清理"

    # ---- Tier 1: regex fast-path ----
    if text == "帮助":
        return HELP_TEXT

    if text == "提醒":
        from app.services.scheduler import send_manual_reminder
        return await send_manual_reminder(chatid)

    if text in ("列表", "任务"):
        return handle_list_regex(chatid)

    task_id = parse_complete_command(text)
    if task_id is not None:
        return handle_complete_regex(task_id, chatid)

    parsed = parse_create_command(text)
    if parsed:
        reply, new_task_id = handle_create_regex(parsed, chatid)
        # Fire-and-forget memory store (don't block fast path)
        ddl_str = parsed["ddl_time"].strftime("%Y-%m-%d %H:%M") if parsed.get("ddl_time") else ""
        asyncio.create_task(
            memory_service.store(
                chatid,
                f"创建了任务「{parsed['title']}」DDL {ddl_str}",
                "task",
            )
        )
        # Auto-decompose if task has description
        if parsed.get("description"):
            asyncio.create_task(_decompose_and_notify(chatid, new_task_id))
        return reply

    if is_cleanup_command(text):
        reply = handle_cleanup_list(chatid)
        # Set conversation state for confirmation
        conv_manager.create(chatid, intent="cleanup_confirm")
        return reply

    if text == "文件":
        return handle_file_all_regex(chatid)

    ftask_id = parse_file_list_command(text)
    if ftask_id is not None:
        return handle_file_list_regex(ftask_id, chatid)

    assoc = parse_associate_command(text)
    if assoc is not None:
        return handle_associate_regex(assoc[0], assoc[1], chatid)

    # ---- Tier 2: LLM NLU ----
    history = list(conv.messages) if conv and conv.intent == "create_task" else None

    # Only retrieve memories for task-related messages (avoid latency on casual chat)
    memories: list[str] = []
    if _is_task_related(text) or (conv and conv.intent == "create_task"):
        memories = await memory_service.search(chatid, text, top_k=3)

    result = await llm_parse_message(text, history, chatid, memories=memories if memories else None)

    if result.has_tool_calls:
        conv_manager.delete(chatid)

        # ---- Handle send_files separately (needs _client for SDK calls) ----
        send_files_calls = [tc for tc in result.tool_calls if tc.name == "send_files"]

        send_files_replies = []
        for tc in send_files_calls:
            db = SessionLocal()
            try:
                notice, file_infos = execute_send_files(chatid, tc.arguments, db)
                send_files_replies.append(notice)
                # Send each file via SDK — copy to temp with clean name first
                # to avoid the UUID prefix in stored_path leaking to WeChat UI
                for fi in file_infos:
                    src_path = Path(BASE_UPLOAD_DIR) / fi["stored_path"]
                    if not src_path.exists():
                        send_files_replies.append(f"  ❌ {fi['label']} 文件已丢失")
                        continue

                    # Build clean temp filename: {display_label}{suffix}
                    suffix = src_path.suffix
                    clean_name = fi["label"] + (suffix if not fi["label"].endswith(suffix) else "")
                    safe_clean = re.sub(r'[\\/:*?"<>|]', '_', clean_name)

                    tmp_dir = Path(tempfile.mkdtemp(prefix="ddlk_"))
                    tmp_path = tmp_dir / safe_clean
                    try:
                        shutil.copy2(src_path, tmp_path)
                        if frame:
                            await _client.reply_media(frame, str(tmp_path))
                        else:
                            await _client.send_media_message(chatid, str(tmp_path))
                    except Exception as e:
                        print(f"[Bot] Failed to send file {fi['label']}: {e}")
                        send_files_replies.append(f"  ❌ {fi['label']} 发送失败")
                    finally:
                        # Clean up temp copy
                        try:
                            shutil.rmtree(tmp_dir, ignore_errors=True)
                        except Exception:
                            pass
            except Exception as e:
                print(f"[Bot] send_files error: {e}")
                send_files_replies.append(f"文件发送失败：{e}")
            finally:
                db.close()

        # ---- Execute remaining tool calls ----
        reply = execute_tool_calls(result, chatid)

        # Prepend send_files notices (only if there were any)
        if send_files_replies:
            reply = "\n\n".join(send_files_replies) + ("\n\n" + reply if reply else "")

        # Fire-and-forget memory stores + auto-decompose (don't delay response)
        for tc in result.tool_calls:
            if tc.name == "create_task":
                title = tc.arguments.get("title", "")
                ddl = tc.arguments.get("ddl_time", "")
                content = f"创建了任务「{title}」DDL {ddl}"
                asyncio.create_task(memory_service.store(chatid, content, "task"))
                # Auto-decompose: find the just-created task and break it down
                db2 = SessionLocal()
                try:
                    new_task = (
                        db2.query(Task)
                        .filter(Task.chatid == chatid, Task.title == title, Task.status == "pending")
                        .order_by(Task.id.desc())
                        .first()
                    )
                    if new_task:
                        asyncio.create_task(_decompose_and_notify(chatid, new_task.id))
                finally:
                    db2.close()
            elif tc.name == "complete_task":
                hint = tc.arguments.get("title_hint", "")
                tid = tc.arguments.get("task_id", "")
                content = f"完成了任务「{hint}」" if hint else f"完成了任务 #{tid}"
                asyncio.create_task(memory_service.store(chatid, content, "completion"))

            elif tc.name == "associate_file":
                hint = tc.arguments.get("title_hint", "")
                # Clean file queue — the latest file was just associated
                if _file_queue.get(chatid):
                    _file_queue[chatid].pop()  # remove most recent (LIFO for latest)
                    if not _file_queue[chatid]:
                        del _file_queue[chatid]
                content = f"关联了文件到任务「{hint}」"
                asyncio.create_task(memory_service.store(chatid, content, "task"))

        # Store conversation memory for multi-turn create_task
        if conv and conv.intent == "create_task" and len(conv.messages) >= 2:
            last_user = conv.messages[-1].get("content", "") if conv.messages else ""
            summary = f"用户通过{len(conv.messages) // 2}轮对话创建了任务: {last_user[:100]}"
            asyncio.create_task(memory_service.store(chatid, summary, "conversation"))

        if result.reply and result.reply.strip():
            return f"{result.reply.strip()}\n\n{reply}"
        return reply

    # No tool calls — follow-up or chat
    if _is_task_related(text) or (conv and conv.intent == "create_task"):
        if conv is None:
            conv = conv_manager.create(chatid, intent="create_task")
        conv.messages.append({"role": "user", "content": text})
        if result.reply:
            conv.messages.append({"role": "assistant", "content": result.reply})
        conv_manager.update(chatid, created_at=conv.created_at)

    return result.reply or "没太理解，试试用「帮助」查看用法？"


def _is_command_or_task(text: str) -> bool:
    """Check if text looks like an explicit command — used as escape hatch
    from file-association hint matching."""
    t = text.strip()
    # Fixed commands
    if t in ("帮助", "列表", "任务", "文件", "清理", "清理已完成", "确认清理", "提醒"):
        return True
    # Regex command patterns
    if parse_complete_command(t) is not None:
        return True
    if parse_create_command(t) is not None:
        return True
    if parse_file_list_command(t) is not None:
        return True
    if parse_associate_command(t) is not None:
        return True
    # Note: NO LONGER includes _is_task_related() — task-related text
    # should flow to LLM, not be treated as a command escape hatch
    return False


def _is_task_related(text: str) -> bool:
    """Check if message is about creating/querying tasks (not files, not commands)."""
    task_keywords = ["任务", "ddl", "作业", "论文", "考试", "答辩", "报告", "提交",
                     "截止", "交", "汇报", "实验", "项目", "pre", "due", "明天",
                     "下周", "周五", "周六", "周日", "周一", "周二", "周三", "周四",
                     "这周", "月底", "月初", "待办", "没做完",
                     "文件", "附件", "发我", "传", "发送"]
    lower = text.lower()
    return any(kw in lower for kw in task_keywords)


# ---------------------------------------------------------------------------
# Task auto-decomposition (V3)
# ---------------------------------------------------------------------------


async def _decompose_and_notify(chatid: str, task_id: int):
    """Decompose a task into substeps and send results to user proactively.

    Fire-and-forget: called after task creation or file association.
    Failures are logged and swallowed — never crashes the bot.
    """
    try:
        from app.services.task_analyzer import decompose_task
        from app.services.display import format_task_decomposition

        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                return
            files = list(
                db.query(TaskFile)
                .filter(TaskFile.chatid == chatid, TaskFile.task_id == task_id)
                .all()
            )
        finally:
            db.close()

        subtasks = await decompose_task(
            task_title=task.title,
            task_description=task.description,
            files=files,
            ddl_time=task.ddl_time,
        )

        if not subtasks:
            return

        message = format_task_decomposition(task, subtasks)
        client = get_client()
        if client:
            await client.send_message(chatid, {
                "chatid": chatid,
                "msgtype": "markdown",
                "markdown": {"content": message},
            })
            print(f"[Bot] Sent task decomposition for #{task_id} to {chatid[:8]}...")

    except Exception as e:
        print(f"[Bot] Task decomposition failed for #{task_id}: {e}")


# ---------------------------------------------------------------------------
# WeChat Work WebSocket event handlers
# ---------------------------------------------------------------------------


async def on_message(frame):
    """Handle incoming message from WeChat Work."""
    body = frame.body
    if not body or not isinstance(body, dict):
        return

    msgtype = body.get("msgtype", "")
    chatid = body.get("chatid") or body.get("from", {}).get("userid", "")

    if msgtype == "text":
        content = body.get("text", {}).get("content", "")
        if not content:
            return
        print(f"[Bot] Received: {content[:80]} | chatid={chatid}")
        reply_text = await handle_message(content, chatid, frame)
        print(f"[Bot] Reply: {reply_text[:80]}")

    elif msgtype == "image":
        img = body.get("image", {})
        print(f"[Bot] Received image | chatid={chatid}")
        reply_text = await handle_file_message(frame, chatid, "image", img)
        print(f"[Bot] Reply: {reply_text[:80]}")

    elif msgtype == "file":
        file_info = body.get("file", {})
        print(f"[Bot] Received file | chatid={chatid} name={file_info.get('name', '?')}")
        reply_text = await handle_file_message(frame, chatid, "file", file_info)
        print(f"[Bot] Reply: {reply_text[:80]}")

    elif msgtype == "event":
        event = body.get("event", {})
        event_type = event.get("eventtype", "")
        print(f"[Bot] Event: {event_type}")

    elif msgtype == "stream":
        pass

    else:
        return

    if msgtype in ("text", "image", "file"):
        await _client.reply(frame, {
            "chatid": chatid,
            "msgtype": "markdown",
            "markdown": {"content": reply_text},
        })


_client: WSClient | None = None


def get_client() -> WSClient | None:
    """Return the global WSClient instance (may be None if bot not connected)."""
    return _client


async def run_bot(bot_id: str, secret: str):
    """Main bot entry point using the official WeCom AI Bot SDK."""
    global _client

    options = WSClientOptions(bot_id=bot_id, secret=secret)
    _client = WSClient(options)

    _client.on("message", on_message)

    async def _on_any(frame):
        body_keys = list(frame.body.keys()) if isinstance(frame.body, dict) else "N/A"
        print(f"[Bot] Event: cmd={frame.cmd} body_keys={body_keys}")
    _client.on("*", _on_any)

    print("[Bot] Connecting via official SDK...")
    await _client.connect_async()
    print("[Bot] Connected and authenticated.")
