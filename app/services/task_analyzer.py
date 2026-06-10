"""Auto task decomposition — extract file content + LLM analysis.

When a task is created with a description or files are associated,
this module breaks the task into actionable sub-steps with time estimates.

Entry point: decompose_task(task_title, task_description, files)
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
LLM_TIMEOUT = 15.0  # longer than message parsing, this is background
MAX_FILE_CONTENT_CHARS = 3000

# ---------------------------------------------------------------------------
# File content extraction
# ---------------------------------------------------------------------------


def extract_file_content(file_path: str, file_type: str = "file") -> str:
    """Extract text content from a file. Returns empty string on failure.

    Supports: PDF, DOCX, TXT. Images are skipped.
    """
    if not file_path or not os.path.exists(file_path):
        return ""

    suffix = Path(file_path).suffix.lower()

    try:
        if suffix == ".pdf":
            return _extract_pdf(file_path)
        elif suffix in (".docx", ".doc"):
            return _extract_docx(file_path)
        elif suffix in (".txt", ".md", ".py", ".c", ".cpp", ".java", ".json", ".csv"):
            return _extract_txt(file_path)
        else:
            # Unknown type — try as text
            return _extract_txt(file_path)
    except Exception as e:
        print(f"[Analyzer] File extraction failed for {file_path}: {e}")
        return ""


def _extract_pdf(path: str) -> str:
    """Extract text from PDF using PyPDF2."""
    from PyPDF2 import PdfReader

    reader = PdfReader(path)
    pages = []
    for page in reader.pages[:10]:  # max 10 pages
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n".join(pages)[:MAX_FILE_CONTENT_CHARS]


def _extract_docx(path: str) -> str:
    """Extract text from DOCX using python-docx."""
    from docx import Document

    doc = Document(path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)[:MAX_FILE_CONTENT_CHARS]


def _extract_txt(path: str) -> str:
    """Read text file with encoding fallback."""
    for encoding in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read(MAX_FILE_CONTENT_CHARS)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""


# ---------------------------------------------------------------------------
# LLM task decomposition
# ---------------------------------------------------------------------------

DECOMPOSE_PROMPT = """你是一个任务分解专家。用户有一个大学课程任务需要拆解。

任务信息：
- 标题：{task_title}
- 截止时间：{ddl_str}
- 剩余时间：{hours_left}小时
- 描述：{task_description}
{file_content_section}

请将这个任务分解为具体的子任务步骤，要求：
1. 每个子任务应该是可独立完成的具体行动（不要拆太细，3-8个为佳）
2. 根据子任务的复杂度估算所需时间（小时）
3. 按照推荐的执行顺序排列
4. 考虑到截止时间，给出合理的分配建议

请严格按照以下JSON格式输出，不要输出其他内容：
{{"subtasks": [{{"title": "子任务标题", "estimated_hours": 2.0, "difficulty": 5.0, "order": 1}}, ...]}}"""


async def analyze_task(
    task_title: str,
    task_description: str,
    file_contents: str = "",
    ddl_time: datetime | None = None,
) -> list[dict] | None:
    """Call LLM to break down a task into subtasks.

    Returns list of subtask dicts, or None on failure.
    Each dict: {"title": str, "estimated_hours": float, "difficulty": float, "order": int}
    """
    if not DEEPSEEK_API_KEY:
        return None

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if ddl_time:
        hours_left = max((ddl_time - now).total_seconds() / 3600, 0)
        ddl_str = ddl_time.strftime("%Y-%m-%d %H:%M")
    else:
        hours_left = 72
        ddl_str = "未设定"

    file_section = ""
    if file_contents.strip():
        file_section = f"- 相关文档内容：\n{file_contents[:MAX_FILE_CONTENT_CHARS]}"
    else:
        file_section = "- 没有相关附件"

    prompt = DECOMPOSE_PROMPT.format(
        task_title=task_title,
        task_description=task_description or "无",
        ddl_str=ddl_str,
        hours_left=f"{hours_left:.1f}",
        file_content_section=file_section,
    )

    client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            ),
            timeout=LLM_TIMEOUT,
        )
        content = response.choices[0].message.content or ""
        return _parse_subtasks_json(content)
    except asyncio.TimeoutError:
        print("[Analyzer] LLM timeout on task decomposition")
        return None
    except Exception as e:
        print(f"[Analyzer] LLM error: {e}")
        return None


def _parse_subtasks_json(content: str) -> list[dict] | None:
    """Parse subtasks from LLM response. Handles raw JSON and markdown-wrapped JSON."""
    # Try direct parse
    try:
        data = json.loads(content)
        if "subtasks" in data:
            return data["subtasks"]
        return None
    except json.JSONDecodeError:
        pass

    # Try extracting JSON block from markdown
    match = re.search(r"\{[^{}]*\"subtasks\"[^{}]*\[.*?\]\s*\}", content, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if "subtasks" in data:
                return data["subtasks"]
        except json.JSONDecodeError:
            pass

    # Try finding array directly
    match = re.search(r"\[.*?\]", content, re.DOTALL)
    if match:
        try:
            arr = json.loads(match.group())
            if isinstance(arr, list) and arr and "title" in arr[0]:
                return arr
        except (json.JSONDecodeError, KeyError, IndexError):
            pass

    print(f"[Analyzer] Failed to parse subtasks JSON from: {content[:200]}")
    return None


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


async def decompose_task(
    task_title: str,
    task_description: str,
    files: list | None = None,
    ddl_time: datetime | None = None,
) -> list[dict] | None:
    """Full pipeline: extract file content -> LLM analysis -> return subtasks.

    Args:
        task_title: Task title
        task_description: Task description (may be None)
        files: List of TaskFile objects (with stored_path attribute)
        ddl_time: Task deadline

    Returns:
        List of subtask dicts or None if decomposition fails/unnecessary.
    """
    # Skip if no meaningful content to analyze
    has_desc = task_description and len(task_description.strip()) > 10
    has_files = files and len(files) > 0

    if not has_desc and not has_files:
        return None

    # Extract file content (sync, run in thread to avoid blocking)
    file_content = ""
    if has_files:
        from app.database.database import SessionLocal
        from app.models.task_file import TaskFile

        for f in files[:3]:  # max 3 files to avoid token explosion
            stored = f.stored_path if hasattr(f, "stored_path") else ""
            if stored:
                from app.services.file_manager import BASE_UPLOAD_DIR

                full_path = os.path.join(BASE_UPLOAD_DIR, stored)
                extracted = await asyncio.to_thread(
                    extract_file_content, full_path, getattr(f, "file_type", "file")
                )
                if extracted:
                    file_content += f"\n--- {getattr(f, 'original_name', '文件')} ---\n"
                    file_content += extracted

    # Call LLM
    subtasks = await analyze_task(
        task_title=task_title,
        task_description=task_description or "",
        file_contents=file_content[:MAX_FILE_CONTENT_CHARS],
        ddl_time=ddl_time,
    )

    return subtasks
