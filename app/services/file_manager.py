"""File download, storage, and task association for WeChat Work bot."""

import os
import re
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.database.database import SessionLocal
from app.models.task_file import TaskFile
from app.models.task import Task

BASE_UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")


# ---------------------------------------------------------------------------
# Label generation — extract readable name from messy filename
# ---------------------------------------------------------------------------


def _generate_label(original_name: str, file_type: str = "file") -> str:
    """Extract a clean display label from a chaotic filename.

    Strips common noise patterns (timestamps, IMG_ prefix, UUIDs, WeChat
    prefixes) and returns a human-readable short label.
    """
    stem = Path(original_name).stem

    # Normalize separators
    stem = re.sub(r'[_\-]+', ' ', stem)

    # Remove whole noise patterns: prefix + optional trailing chars
    noise_patterns = [
        r'\bIMG\S*\b',             # IMG / IMG_20240101 / IMG-20240101-WA0001
        r'\bScreenshot\S*\b',      # Screenshot_2024-01-01
        r'\bwx[\s_-]*camera\S*\b', # wx_camera_xxx
        r'\bmmexport\S*\b',        # mmexport1704067200000
        r'\bmicroMsg\S*\b',        # microMsg.1704067200000 (WeChat Android)
        r'\b微信\s*(图片|截图|视频|语音|文档|文件)?\S*\b',
        r'\bQQ\s*(图片|截图|视频)?\S*\b',
        r'\bphoto\S*\b',           # photo / photo_abcd1234
        r'\bcapture\S*\b',         # capture_123
        r'\bsnap\S*\b',            # snap_123
        r'\bcamera\S*\b',          # camera_photo_xxx
        r'\btmp[\s_-]*\S*\b',      # tmp / tmp_1704067200000
        r'\b未命名\b',
    ]
    for pat in noise_patterns:
        stem = re.sub(pat, '', stem, flags=re.IGNORECASE)

    # Remove long digit sequences (timestamps, IDs)
    # (?a) = ASCII-only mode: prevents \b from matching between digits and CJK chars
    stem = re.sub(r'(?a)\b\d{8,}\b', '', stem)
    # Remove date patterns
    stem = re.sub(r'(?a)\b\d{2,4}[-/]\d{2}[-/]\d{2}\b', '', stem)
    # Remove short prefix + digits noise: WA0001, P12345, etc.
    stem = re.sub(r'(?a)\b[A-Za-z]{1,3}\d{2,}\b', '', stem)
    # Remove remaining standalone numbers (ASCII-only to avoid splitting CJK)
    stem = re.sub(r'(?a)\b\d+\b', '', stem)
    # Remove hex fragments and UUID-like strings
    stem = re.sub(r'(?a)\b[a-f0-9]{8,}\b', '', stem, flags=re.IGNORECASE)

    # Clean up
    stem = re.sub(r'\s+', ' ', stem).strip(' ._-')

    if len(stem) < 2:
        return {"image": "图片", "file": "文档"}.get(file_type, "文件")

    return stem[:40]


def _is_garbled_name(name: str) -> bool:
    """Detect if a filename is meaningless (hash, UUID fragment, pure digits, etc.).

    Returns True for filenames that look machine-generated and should be replaced
    with a task-derived label.
    """
    stem = Path(name).stem

    # Pure digits or very short
    if stem.isdigit() or len(stem) < 3:
        return True

    # Looks like a hex hash: 32+ hex chars, or 8+ hex with no Chinese/alphabet words
    hex_ratio = sum(1 for c in stem if c in "0123456789abcdefABCDEF") / max(len(stem), 1)
    if hex_ratio > 0.7 and len(stem) >= 8:
        return True

    # Contains UUID-like patterns (8-4-4-4-12)
    import re as _re
    if _re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", stem, _re.IGNORECASE):
        return True

    # WeChat camera roll patterns
    if _re.match(r"^(wx_camera|mmexport|microMsg|IMG_|Screenshot_|photo_|snap_|capture_|camera_|tmp[\s_-])", stem, _re.IGNORECASE):
        return True

    # Timestamp-like: starts with a long digit sequence
    if _re.match(r"^\d{8,}", stem):
        return True

    return False


def rename_file_label(chatid: str, file_id: int, new_label: str) -> str:
    """Set a custom label for a file. Returns status message."""
    db = SessionLocal()
    try:
        task_file = (
            db.query(TaskFile)
            .filter(TaskFile.id == file_id, TaskFile.chatid == chatid)
            .first()
        )
        if task_file is None:
            return f"文件 {file_id} 不存在"

        old = task_file.label or task_file.original_name
        task_file.label = new_label.strip()[:40]
        db.commit()
        return f"文件 {file_id} 已重命名：{old[:20]} → {task_file.label}"
    finally:
        db.close()


def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


async def download_and_save(
    client,            # WSClient instance (for download_file)
    chatid: str,
    file_type: str,    # "image" | "file"
    metadata: dict,    # {"url": ..., "aeskey": ..., "name": ...}
) -> TaskFile:
    """Download a file from WeChat, save to disk, return TaskFile record."""

    url = metadata.get("url", "")
    aes_key = metadata.get("aeskey", "")
    original_name = metadata.get("name", f"{file_type}_{uuid.uuid4().hex[:8]}")

    if not url:
        raise ValueError("No URL in file metadata")

    # Download + decrypt via SDK
    buffer, extracted_name = await client.download_file(url, aes_key)
    if extracted_name:
        original_name = extracted_name

    # Generate storage path
    suffix = Path(original_name).suffix or (".jpg" if file_type == "image" else "")
    safe_name = f"{uuid.uuid4().hex}_{Path(original_name).stem}{suffix}"
    relative_path = f"{chatid}/unassigned/{safe_name}"
    full_path = Path(BASE_UPLOAD_DIR) / relative_path
    _ensure_dir(full_path.parent)

    # Write to disk
    full_path.write_bytes(buffer)

    # Create DB record (unassigned initially)
    db = SessionLocal()
    try:
        task_file = TaskFile(
            chatid=chatid,
            original_name=original_name,
            label=_generate_label(original_name, file_type),
            stored_path=relative_path,
            file_type=file_type,
            size=len(buffer),
        )
        db.add(task_file)
        db.commit()
        db.refresh(task_file)
        return task_file
    finally:
        db.close()


def list_task_files(chatid: str, task_id: int) -> list[TaskFile]:
    """Return all files attached to a task."""
    db = SessionLocal()
    try:
        return (
            db.query(TaskFile)
            .filter(TaskFile.chatid == chatid, TaskFile.task_id == task_id)
            .order_by(TaskFile.created_at.desc())
            .all()
        )
    finally:
        db.close()


def list_unassigned_files(chatid: str) -> list[TaskFile]:
    """Return all unassigned files for this chatid."""
    db = SessionLocal()
    try:
        return (
            db.query(TaskFile)
            .filter(TaskFile.chatid == chatid, TaskFile.task_id.is_(None))
            .order_by(TaskFile.created_at.desc())
            .all()
        )
    finally:
        db.close()


def list_all_files(chatid: str) -> dict:
    """Return all files grouped by task.

    Returns:
        {task_id: {"task_title": str, "files": [TaskFile, ...]}}
        Key None = unassigned files.
    """
    db = SessionLocal()
    try:
        files = (
            db.query(TaskFile)
            .filter(TaskFile.chatid == chatid)
            .order_by(TaskFile.created_at.desc())
            .all()
        )

        grouped: dict = {}
        for f in files:
            key = f.task_id  # None for unassigned
            if key not in grouped:
                if key is None:
                    grouped[key] = {"task_title": None, "files": []}
                else:
                    task = db.query(Task).filter(Task.id == key).first()
                    grouped[key] = {
                        "task_title": task.title if task else f"任务{key}",
                        "files": [],
                    }
            grouped[key]["files"].append(f)

        return grouped
    finally:
        db.close()


def get_recent_tasks(chatid: str, limit: int = 5) -> list[Task]:
    """Return recent pending tasks (for file association prompt)."""
    db = SessionLocal()
    try:
        return (
            db.query(Task)
            .filter(Task.chatid == chatid, Task.status == "pending")
            .order_by(Task.created_at.desc())
            .limit(limit)
            .all()
        )
    finally:
        db.close()


def associate_file(chatid: str, file_id: int, task_id: int) -> str:
    """Manually associate a file with a task. Returns status message."""
    db = SessionLocal()
    try:
        task_file = (
            db.query(TaskFile)
            .filter(TaskFile.id == file_id, TaskFile.chatid == chatid)
            .first()
        )
        if task_file is None:
            return f"文件 {file_id} 不存在"

        task = (
            db.query(Task)
            .filter(Task.id == task_id, Task.chatid == chatid)
            .first()
        )
        if task is None:
            return f"任务 {task_id} 不存在"

        task_file.task_id = task_id

        # Auto-rename garbled files to task-derived label
        if _is_garbled_name(task_file.original_name):
            type_name = {"image": "图片", "file": "文档"}.get(task_file.file_type, "文件")
            task_file.label = f"{task.title}_{type_name}"[:40]

        db.commit()

        # Move file to task directory
        old_path = Path(BASE_UPLOAD_DIR) / task_file.stored_path
        new_relative = f"{chatid}/{task_id}/{Path(task_file.stored_path).name}"
        new_path = Path(BASE_UPLOAD_DIR) / new_relative
        _ensure_dir(new_path.parent)
        if old_path.exists():
            old_path.rename(new_path)
        task_file.stored_path = new_relative
        db.commit()

        display_name = task_file.label or task_file.original_name
        return f"文件「{display_name}」已关联到任务「{task.title}」"
    finally:
        db.close()


def associate_file_by_hint(chatid: str, file_id: int, hint: str) -> str:
    """Associate using text hint (task ID, name keyword, or fuzzy match)."""
    # 1. Try parse as task ID
    try:
        return associate_file(chatid, file_id, int(hint))
    except ValueError:
        pass

    db = SessionLocal()
    try:
        pending = (
            db.query(Task)
            .filter(Task.chatid == chatid, Task.status == "pending")
            .order_by(Task.created_at.desc())
            .all()
        )

        if not pending:
            return "暂无待办任务可以关联"

        # 2. Exact reverse match: user types "uml任务的" → task title "UML任务" is inside hint
        hint_lower = hint.lower()
        reverse_matches = [t for t in pending if t.title.lower() in hint_lower]
        if len(reverse_matches) == 1:
            db.close()
            return associate_file(chatid, file_id, reverse_matches[0].id)
        if len(reverse_matches) > 1:
            lines = ["找到以下匹配任务，请回复编号：\n"]
            for t in reverse_matches:
                lines.append(f"{t.id}. {t.title}")
            return "\n".join(lines)

        # 3. Keyword extraction: split by common stop words and try each meaningful chunk
        stop_words = {"这是", "那个", "的文件", "任务", "了", "是", "的", "这个",
                      "一个", "什么", "哪个", "文件", "附件", "就是", "上面", "刚才"}
        keywords = [hint]
        for sw in stop_words:
            new_kw = []
            for kw in keywords:
                new_kw.extend(kw.split(sw))
            keywords = [k.strip() for k in new_kw if k.strip()]
        # Also try original hint as-is
        keywords.insert(0, hint)

        # 4. Try each keyword with ilike
        matched_ids = set()
        for kw in keywords:
            if len(kw) < 2:
                continue
            matches = [t for t in pending if kw.lower() in t.title.lower()]
            for t in matches:
                matched_ids.add(t.id)

        if len(matched_ids) == 1:
            task_id = matched_ids.pop()
            db.close()
            return associate_file(chatid, file_id, task_id)

        if len(matched_ids) > 1:
            matched_tasks = [t for t in pending if t.id in matched_ids]
            lines = ["找到以下匹配任务，请回复编号：\n"]
            for t in matched_tasks:
                lines.append(f"{t.id}. {t.title}")
            return "\n".join(lines)

        # 5. No match — show all pending tasks and ask to pick by ID
        lines = [f"没找到和「{hint}」匹配的任务。最近的任务有：\n"]
        for t in pending[:8]:
            ddl = t.ddl_time.strftime("%m/%d %H:%M") if t.ddl_time else "?"
            lines.append(f"  {t.id}. {t.title} ({ddl})")
        lines.append("\n请回复任务编号")
        return "\n".join(lines)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cleanup — delete files for completed tasks
# ---------------------------------------------------------------------------


def list_completed_task_files(chatid: str) -> dict:
    """List files belonging to completed tasks, grouped by task.

    Returns:
        {task_id: {"task_title": str, "files": [TaskFile, ...], "total_size": int}}
    """
    db = SessionLocal()
    try:
        # Find completed task IDs for this chat
        completed_ids = [
            row[0]
            for row in db.query(Task.id)
            .filter(Task.chatid == chatid, Task.status == "completed")
            .all()
        ]
        if not completed_ids:
            return {}

        files = (
            db.query(TaskFile)
            .filter(
                TaskFile.chatid == chatid,
                TaskFile.task_id.in_(completed_ids),
            )
            .order_by(TaskFile.task_id, TaskFile.created_at.desc())
            .all()
        )

        grouped: dict = {}
        for f in files:
            key = f.task_id
            if key not in grouped:
                task = db.query(Task).filter(Task.id == key).first()
                grouped[key] = {
                    "task_title": task.title if task else f"任务{key}",
                    "files": [],
                    "total_size": 0,
                }
            grouped[key]["files"].append(f)
            grouped[key]["total_size"] += f.size

        return grouped
    finally:
        db.close()


def delete_files_for_tasks(chatid: str, task_ids: list[int]) -> int:
    """Delete all files (disk + DB) for given task IDs. Returns count."""
    db = SessionLocal()
    try:
        files = (
            db.query(TaskFile)
            .filter(
                TaskFile.chatid == chatid,
                TaskFile.task_id.in_(task_ids),
            )
            .all()
        )

        deleted = 0
        for f in files:
            # Delete from disk
            file_path = Path(BASE_UPLOAD_DIR) / f.stored_path
            try:
                if file_path.exists():
                    file_path.unlink()
            except OSError as e:
                print(f"[File] Failed to delete {f.stored_path}: {e}")

            # Delete DB record
            db.delete(f)
            deleted += 1

        db.commit()
        return deleted
    except Exception as e:
        db.rollback()
        print(f"[File] Cleanup failed: {e}")
        return 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# V4: Latest-file association (queue-based)
# ---------------------------------------------------------------------------


def get_latest_unassociated(chatid: str) -> TaskFile | None:
    """Return the most recent unassociated file for a chatid."""
    db = SessionLocal()
    try:
        return (
            db.query(TaskFile)
            .filter(TaskFile.chatid == chatid, TaskFile.task_id.is_(None))
            .order_by(TaskFile.created_at.desc())
            .first()
        )
    finally:
        db.close()


def list_unassociated(chatid: str) -> list[TaskFile]:
    """Return all unassociated files for a chatid (newest first)."""
    db = SessionLocal()
    try:
        return (
            db.query(TaskFile)
            .filter(TaskFile.chatid == chatid, TaskFile.task_id.is_(None))
            .order_by(TaskFile.created_at.desc())
            .all()
        )
    finally:
        db.close()


def associate_latest_file(chatid: str, task_id: int) -> str:
    """Associate the most recent unassociated file with a task."""
    tf = get_latest_unassociated(chatid)
    if tf is None:
        return "没有待关联的文件"
    return associate_file(chatid, tf.id, task_id)
