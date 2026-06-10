"""Short code generator for tasks — user-facing readable identifiers.

Converts task titles into concise codes like "DB-LAB4", "JAVA-PROJECT".
Used for natural language references instead of numeric IDs.

Strategy (in priority order):
1. LLM-based generation (most accurate for Chinese titles)
2. English keyword extraction (if title contains English)
3. Fallback: truncated uppercase title

Examples:
  数据库实验4       → DB-LAB4
  JavaWeb课程设计   → JAVA-PROJECT
  数据挖掘期末考试  → DM-FINAL
  检查Javaweb系统   → JAVAWEB-CHECK
"""

import asyncio
import os
import re
from typing import Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Characters allowed in short codes
_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9\-]{1,15}$")

# LLM prompt for code generation
_CODE_PROMPT = """将以下任务标题转换为一个简短的英文标识码（short code）。

规则：
1. 只用大写字母、数字和连字符
2. 2-12个字符
3. 能让人一眼认出是什么任务
4. 格式示例：DB-LAB4, JAVA-PROJECT, DM-FINAL, DS-REPORT

任务标题：{title}

只输出标识码，不要输出其他内容。"""


async def generate_short_code(title: str, existing_codes: set[str] | None = None) -> str:
    """Generate a short code for a task title (async, uses LLM).

    Tries LLM first, falls back to heuristic.
    Ensures uniqueness against existing_codes if provided.
    """
    if not title:
        return _fallback_code("", existing_codes)

    # Try LLM
    if DEEPSEEK_API_KEY:
        code = await _llm_generate(title)
        if code and _is_valid_code(code):
            code = _ensure_unique(code, existing_codes)
            return code

    # Fallback to heuristic
    return _heuristic_code(title, existing_codes)


def generate_short_code_sync(title: str, existing_codes: set[str] | None = None) -> str:
    """Generate a short code without LLM (sync, for fast-path regex commands).

    Uses heuristic only — no API call, no latency.
    """
    if not title:
        return _fallback_code("", existing_codes)
    return _heuristic_code(title, existing_codes)


async def _llm_generate(title: str) -> Optional[str]:
    """Call LLM to generate a short code."""
    client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": _CODE_PROMPT.format(title=title)}],
                temperature=0.1,
                max_tokens=20,
            ),
            timeout=5.0,
        )
        raw = response.choices[0].message.content or ""
        # Clean: take first line, strip whitespace, uppercase
        code = raw.strip().split("\n")[0].strip().upper()
        # Remove any non-alphanumeric except hyphens
        code = re.sub(r"[^A-Z0-9\-]", "", code)
        return code
    except Exception as e:
        print(f"[ShortCode] LLM failed: {e}")
        return None


# Common subject abbreviations (Chinese prefix → English code)
_SUBJECT_MAP = {
    "数据库": "DB",
    "数据挖掘": "DM",
    "数据结构": "DS",
    "操作系统": "OS",
    "计算机网络": "NET",
    "计算机组成": "CO",
    "编译原理": "COMPILER",
    "软件工程": "SE",
    "人工智能": "AI",
    "机器学习": "ML",
    "深度学习": "DL",
    "线性代数": "LA",
    "高等数学": "MATH",
    "大学物理": "PHY",
    "大学英语": "ENG",
    "概率论": "PROB",
    "离散数学": "DMATH",
    "数字电路": "DIGITAL",
    "信号与系统": "SIGNAL",
    "通信原理": "COMM",
    "嵌入式": "EMB",
    "微机原理": "MCU",
    "JavaWeb": "JAVAWEB",
    "Javaweb": "JAVAWEB",
    "javaweb": "JAVAWEB",
    "Web": "WEB",
    "UML": "UML",
    "SQL": "SQL",
}

# Action/suffix map
_SUFFIX_MAP = {
    "大作业": "PROJECT",
    "课程设计": "PROJECT",
    "实验": "LAB",
    "期末考试": "FINAL",
    "期中考试": "MID",
    "考试": "EXAM",
    "报告": "REPORT",
    "作业": "HW",
    "论文": "PAPER",
    "答辩": "DEFENSE",
    "检查": "CHECK",
    "测试": "TEST",
    "练习": "DRILL",
    "复习": "REVIEW",
    "通知": "NOTIFY",
}


def _heuristic_code(title: str, existing_codes: set[str] | None = None) -> str:
    """Generate code without LLM — extract subject + action pattern."""
    nums = re.findall(r"\d+", title)
    num_str = nums[-1] if nums else ""

    # 1. Try subject + suffix pattern
    subject = _extract_subject(title)
    suffix = _extract_suffix(title)

    if subject and suffix:
        code = f"{subject}-{suffix}{num_str}"
        return _ensure_unique(code, existing_codes)

    if subject:
        code = f"{subject}{num_str}"
        return _ensure_unique(code, existing_codes)

    if suffix:
        code = f"{suffix}{num_str}"
        return _ensure_unique(code, existing_codes)

    # 2. Extract English parts (for mixed titles like "检查Javaweb系统")
    english_parts = re.findall(r"[a-zA-Z]+", title)
    if english_parts:
        main = "".join(p.upper() for p in english_parts[:2])
        code = f"{main}{num_str}" if num_str else main
        return _ensure_unique(code[:12], existing_codes)

    # 3. Pure fallback
    return _fallback_code(title, existing_codes)


def _extract_subject(title: str) -> str | None:
    """Extract subject abbreviation from title."""
    # Check subject map (longest match first)
    for cn, en in sorted(_SUBJECT_MAP.items(), key=lambda x: -len(x[0])):
        if cn in title:
            return en
    return None


def _extract_suffix(title: str) -> str | None:
    """Extract action/suffix from title."""
    # Check suffix map (longest match first — "大作业" before "作业")
    for cn, en in sorted(_SUFFIX_MAP.items(), key=lambda x: -len(x[0])):
        if cn in title:
            return en
    return None


def _fallback_code(title: str, existing_codes: set[str] | None = None) -> str:
    """Last resort: truncate and uppercase."""
    clean = re.sub(r"[^a-zA-Z0-9一-鿿]", "", title)
    if not clean:
        code = "TASK"
    else:
        # Take first 2-4 meaningful chars
        code = clean[:4].upper()
    return _ensure_unique(code, existing_codes)


def _is_valid_code(code: str) -> bool:
    """Check if code matches expected format."""
    return bool(_CODE_PATTERN.match(code))


def _ensure_unique(code: str, existing_codes: set[str] | None = None) -> str:
    """Append a number suffix if code already exists."""
    if not existing_codes or code not in existing_codes:
        return code

    for i in range(2, 100):
        candidate = f"{code}{i}"
        if candidate not in existing_codes:
            return candidate

    # Shouldn't happen, but just in case
    return f"{code}X"
