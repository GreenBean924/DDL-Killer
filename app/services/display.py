"""Shared display formatters for DDL-Killer bot replies.

All markdown panel formatting lives here so bot_ws_client.py (regex fast-path)
and llm_service.py (LLM tool execution) stay in sync — required by CLAUDE.md.

Design principles for mobile-friendly WeChat Work markdown:
- Vertical layout: one piece of info per line, no horizontal multi-column alignment
- Short lines: ≤30 chars ideal, to avoid wrap tearing on narrow screens
- Zero hard alignment: no consecutive spaces, no tab characters, no ··· dividers
- Blank lines between items, not inline separators
- Color: <font color="warning|info|comment"> for urgency

V2 changes:
- Added _ddl_status() for due-date status marking (已过期/今天到期/明天到期)
- Simplified task list: removed difficulty/importance/description from list view
- Simplified file display: no file_id, no icon, just filenames
- Removed label-related display logic (label feature dropped)
"""

from datetime import datetime

from app.models.task import Task
from app.models.task_file import TaskFile

# ---------------------------------------------------------------------------
# Atomic helpers
# ---------------------------------------------------------------------------

_BOTTOM_HINT_TASK = '回复「<font color="info">完成 #编码</font>」或「<font color="info">完成 任务名</font>」'
_BOTTOM_HINT_FILE = '回复「<font color="info">关联 任务名</font>」绑定文件'


def _urgency_icon(risk_score: float) -> str:
    if risk_score >= 70:
        return "🔴"
    elif risk_score >= 40:
        return "🟡"
    else:
        return "🟢"


def file_display_name(f: TaskFile) -> str:
    """Use original filename directly (V2: label feature dropped)."""
    return f.original_name


def _truncate(text: str, max_len: int = 30) -> str:
    return text[:max_len] + "…" if len(text) > max_len else text


def _ddl_status(ddl_time: datetime) -> str:
    """Return a human-readable DDL status with remaining days/hours."""
    now = datetime.now()
    delta = ddl_time - now
    hours = delta.total_seconds() / 3600
    if hours < 0:
        return ' — <font color="warning">已过期</font>'
    elif hours < 24:
        return ' — <font color="warning">⚠️ 紧急！</font>'
    elif hours < 48:
        return ' — <font color="info">明天到期</font>'
    else:
        days = int(hours / 24)
        return f" — 还有{days}天"


def _remaining_label(ddl_time: datetime) -> str:
    """Return '剩余 N 天' for task creation confirmation."""
    now = datetime.now()
    delta = ddl_time - now
    hours = delta.total_seconds() / 3600
    if hours < 0:
        return '<font color="warning">已过期</font>'
    elif hours < 24:
        return '<font color="warning">⚠️ 今天到期</font>'
    elif hours < 48:
        return '<font color="info">明天到期</font>'
    else:
        days = int(hours / 24)
        return f"剩余 {days} 天"


# ---------------------------------------------------------------------------
# Task panels
# ---------------------------------------------------------------------------


def format_task_list(tasks: list[Task], max_show: int = 10) -> str:
    """Pending task list — quote block style, one blank line between tasks."""
    if not tasks:
        return "✅ 暂无待办任务"

    total = len(tasks)
    shown = tasks[:max_show]
    truncated = total - max_show if total > max_show else 0

    lines = [f"📋 待办 · {total}个\n"]

    for t in shown:
        icon = _urgency_icon(t.risk_score)
        ddl = t.ddl_time.strftime("%m/%d %H:%M") if t.ddl_time else "?"
        status = _ddl_status(t.ddl_time) if t.ddl_time else ""
        code = f" #{t.short_code}" if hasattr(t, 'short_code') and t.short_code else ""

        lines.append(f"> {icon} **{t.title}**{code}")
        lines.append(f"> 📅 {ddl}{status}\n")

    if truncated > 0:
        lines.append(f'<font color="comment">...还有 {truncated} 个</font>\n')

    lines.append(_BOTTOM_HINT_TASK)
    return "\n".join(lines)


def format_task_list_with_completed(
    pending: list[Task], completed: list[Task], max_show: int = 10
) -> str:
    """Task list with a collapsed completed section at the bottom."""
    # Build pending section
    if not pending and not completed:
        return "✅ 暂无待办任务"

    parts = []
    if pending:
        parts.append(format_task_list(pending, max_show))
    else:
        parts.append("✅ 暂无待办任务")

    # Append collapsed completed section
    if completed:
        count = len(completed)
        lines = [
            "",
            f'<font color="comment">已完成({count}) ▾</font>',
        ]
        for t in completed[:5]:
            lines.append(f'<font color="comment">  {t.title}</font>')
        if count > 5:
            lines.append(f'<font color="comment">  ...还有 {count - 5} 个</font>')
        parts.append("\n".join(lines))

    return "\n".join(parts)


def format_task_created(task: Task) -> str:
    """Confirmation panel after creating a single task."""
    icon = _urgency_icon(task.risk_score)
    ddl = task.ddl_time.strftime("%Y-%m-%d %H:%M") if task.ddl_time else "?"
    remaining = _remaining_label(task.ddl_time) if task.ddl_time else ""

    code = f" #{task.short_code}" if hasattr(task, 'short_code') and task.short_code else ""
    lines = [
        f"✅ 任务已创建\n",
        f"> {icon} **{task.title}**{code}",
        f"> 📅 DDL: {ddl}",
        f"> 难度: {task.difficulty:.0f} | 重要度: {task.importance:.0f}",
        f"> {remaining}",
    ]
    if task.description:
        lines.append(f"> 💬 {task.description}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File panels
# ---------------------------------------------------------------------------


def format_file_list_by_task(
    files: list[TaskFile], task_id: int, task_name: str
) -> str:
    """Files attached to a single task — just filenames."""
    if not files:
        return f"📎 {task_name} 暂无附件"

    lines = [f"📎 {task_name} · {len(files)}个\n"]
    for f in files:
        name = file_display_name(f)
        lines.append(f"  {_truncate(name, 30)}")

    return "\n".join(lines)


def format_file_panel(grouped: dict) -> str:
    """All files grouped by task.

    Args:
        grouped: {task_id: {"task_title": str, "files": [TaskFile, ...]}}
                 key None = unassigned.
    """
    if not grouped:
        return "暂无文件"

    lines = ["📎 文件管理\n"]

    for task_id, info in sorted(
        grouped.items(), key=lambda x: (x[0] is None, x[0] or 0)
    ):
        files = info["files"]
        count = len(files)

        if task_id is None:
            lines.append(f"未关联 · {count}个")
        else:
            title = info.get("task_title", f"任务{task_id}")
            lines.append(f"{title} · {count}个")

        for f in files:
            name = file_display_name(f)
            lines.append(f"  {_truncate(name, 30)}")

        lines.append("")

    lines.append(_BOTTOM_HINT_FILE)
    return "\n".join(lines)


def format_file_received(task_file: TaskFile, tasks: list[Task], queue_pos: int = 0) -> str:
    """Notice after receiving a file, with which-task-to-associate prompt."""
    name = file_display_name(task_file)

    queue_hint = ""
    if queue_pos > 1:
        queue_hint = f'\n<font color="comment">前面还有 {queue_pos - 1} 个文件待关联</font>'

    lines = [
        f"📥 收到文件\n",
        f"{_truncate(name, 30)}{queue_hint}\n",
    ]

    if tasks:
        lines.append("关联到哪个任务？")
        for t in tasks[:5]:
            icon = _urgency_icon(t.risk_score)
            code = f" #{t.short_code}" if hasattr(t, 'short_code') and t.short_code else ""
            lines.append(f"  {icon} {t.title}{code}")
        lines.append("")
        lines.append("回复任务名即可关联")
    else:
        lines.append('<font color="comment">暂无待办任务</font>')

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cleanup panels
# ---------------------------------------------------------------------------


def format_cleanup_confirm(grouped: dict) -> str:
    """Confirmation panel before deleting completed task files.

    Args:
        grouped: {task_id: {"task_title": str, "files": [TaskFile], "total_size": int}}
    """
    if not grouped:
        return "没有已完成任务的附件"

    total_files = 0
    total_kb = 0
    task_lines = []
    for _, info in grouped.items():
        count = len(info["files"])
        total_files += count
        total_kb += info["total_size"]
        task_lines.append(f"  {info['task_title']} · {count}个")

    lines = [
        "🗑️ 清理确认\n",
        "以下附件将被删除：\n",
        *task_lines,
        "",
        f"共 {total_files} 个文件 · {total_kb / 1024:.0f}KB",
        "",
        '回复「<font color="warning">确认清理</font>」执行删除',
    ]
    return "\n".join(lines)


def format_cleanup_done(file_count: int) -> str:
    """Confirmation after cleanup."""
    return f"🗑️ 已清理 {file_count} 个文件"


def format_files_sending(task_name: str, task_id: int, files: list[TaskFile]) -> str:
    """Notice before/after sending files, with task-context header."""
    file_lines = []
    for f in files:
        name = file_display_name(f)
        file_lines.append(f"  {_truncate(name, 30)}")

    return (
        f"📤 {task_name} · {len(files)}个附件\n\n"
        + "\n".join(file_lines)
        + '\n\n<font color="comment">文件已发送</font>'
    )


# ---------------------------------------------------------------------------
# Reminder & decomposition panels (V3)
# ---------------------------------------------------------------------------


def format_reminder(task: Task, advice: str, hours_left: float) -> str:
    """Proactive reminder message with LLM-generated advice."""
    icon = _urgency_icon(task.risk_score)
    ddl = task.ddl_time.strftime("%m/%d %H:%M") if task.ddl_time else "?"

    lines = [
        "🔥 今日提醒\n",
        f"{icon} **{task.title}**",
        f"> 📅 {ddl} · 剩余{hours_left:.0f}小时\n",
        advice,
    ]
    return "\n".join(lines)


def format_task_decomposition(task: Task, subtasks: list[dict]) -> str:
    """Auto-generated subtask breakdown from task analyzer."""
    ddl = task.ddl_time.strftime("%Y-%m-%d %H:%M") if task.ddl_time else "?"
    total_hours = sum(st.get("estimated_hours", 1) for st in subtasks)
    remaining = _remaining_label(task.ddl_time) if task.ddl_time else ""

    lines = [
        "📋 任务拆解\n",
        f"> **{task.title}**",
        f"> 📅 DDL: {ddl} · {remaining}\n",
    ]

    for st in sorted(subtasks, key=lambda s: s.get("order", 0)):
        diff = st.get("difficulty", 5)
        hours = st.get("estimated_hours", 1)
        order = st.get("order", "?")
        lines.append(f"> {order}️⃣ {st['title']} — 预计 {hours:.0f}h — 难度 {diff:.0f}")

    lines.append("")
    lines.append(f"> 建议分 {len(subtasks)} 步完成，共需约 {total_hours:.0f} 小时。")
    return "\n".join(lines)
