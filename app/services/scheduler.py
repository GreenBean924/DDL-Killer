"""Scheduled smart reminders — proactive task advice via LLM.

Runs as a background asyncio task, started from main.py lifespan.
Checks every 60 seconds if it's time to send reminders (morning 8-9, evening 20-21).
For each user with pending tasks, generates personalized advice via DeepSeek
and sends it proactively via the bot SDK's send_message().

Design:
- Pure asyncio loop, no external scheduler library
- Idempotent: tracks sent status per chatid+date+period in memory
- Resilient: one user's failure doesn't block others
- Rate-limited: 0.5s between sends to avoid API throttling
"""

import asyncio
import os
import random
from datetime import datetime, timezone

from dotenv import load_dotenv
from openai import AsyncOpenAI
from sqlalchemy import text

from app.database.database import SessionLocal
from app.models.task import Task

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Schedule config
MORNING_WINDOW = (8, 10)   # send between 8:00-9:59
EVENING_WINDOW = (20, 22)  # send between 20:00-21:59
CHECK_INTERVAL = 60        # seconds between checks
SEND_DELAY = 0.5           # seconds between per-user sends
LLM_TIMEOUT = 12.0

# Track what we've sent today: {"chatid_20260609_morning": True, ...}
_sent: dict[str, bool] = {}


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def reminder_loop():
    """Main scheduler loop. Runs forever until cancelled."""
    # Wait for bot client to be ready
    if not await _wait_for_client(timeout=120):
        print("[Scheduler] Bot client not ready after 120s, starting anyway (will retry)")

    print("[Scheduler] Reminder loop started")
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)
            await _check_and_send()
        except asyncio.CancelledError:
            print("[Scheduler] Reminder loop cancelled")
            break
        except Exception as e:
            print(f"[Scheduler] Error in reminder loop: {e}")
            # Don't die — keep looping


async def _check_and_send():
    """Check if it's time to send reminders, and send if so."""
    now = datetime.now()  # local time
    hour = now.hour
    date_str = now.strftime("%Y%m%d")

    # Determine which period we're in
    period = None
    if MORNING_WINDOW[0] <= hour < MORNING_WINDOW[1]:
        period = "morning"
    elif EVENING_WINDOW[0] <= hour < EVENING_WINDOW[1]:
        period = "evening"
    else:
        return  # not in a send window

    # Get all active chatids
    chatids = await _get_active_chatids()
    if not chatids:
        return

    print(f"[Scheduler] {period} window active, {len(chatids)} users with pending tasks")

    for chatid in chatids:
        key = f"{chatid}_{date_str}_{period}"
        if _sent.get(key):
            continue

        try:
            task = await _get_top_task(chatid)
            if not task:
                continue

            advice = await _generate_reminder(task)
            if not advice:
                continue

            # Send via bot SDK
            client = _get_bot_client()
            if not client:
                print("[Scheduler] Bot client not available, skipping")
                break  # no point trying other users

            message = _format_reminder_message(task, advice)
            await client.send_message(chatid, {
                "chatid": chatid,
                "msgtype": "markdown",
                "markdown": {"content": message},
            })
            _sent[key] = True
            print(f"[Scheduler] Sent {period} reminder to {chatid[:8]}... for: {task.title}")

            await asyncio.sleep(SEND_DELAY)

        except Exception as e:
            print(f"[Scheduler] Failed to send to {chatid[:8]}...: {e}")
            # Continue with next user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_bot_client():
    """Get the bot WSClient instance (lazy import to avoid circular issues)."""
    from app.services.bot_ws_client import get_client
    return get_client()


async def _wait_for_client(timeout: int = 60) -> bool:
    """Wait until the bot client is connected."""
    for _ in range(timeout):
        client = _get_bot_client()
        if client is not None:
            return True
        await asyncio.sleep(1)
    return False


async def _get_active_chatids() -> list[str]:
    """Get all distinct chatids that have pending tasks."""
    db = SessionLocal()
    try:
        rows = (
            db.query(Task.chatid)
            .filter(Task.status == "pending")
            .distinct()
            .all()
        )
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        print(f"[Scheduler] DB error getting chatids: {e}")
        return []
    finally:
        db.close()


async def send_manual_reminder(chatid: str) -> str:
    """Generate a reminder for the user's top-risk task (manual trigger).

    Returns the formatted reminder message, or a fallback if no tasks.
    """
    print(f"[Scheduler] Manual reminder triggered for chatid={chatid}")
    task = await _get_top_task(chatid)
    if not task:
        print(f"[Scheduler] No pending tasks for chatid={chatid}")
        return "✅ 暂无待办任务，不需要提醒"

    print(f"[Scheduler] Top task: {task.title} (risk={task.risk_score})")
    advice = await _generate_reminder(task)
    if not advice:
        advice = "加油，按时完成！"
    return _format_reminder_message(task, advice)


async def _get_top_task(chatid: str) -> Task | None:
    """Get the highest risk_score pending task for a user."""
    db = SessionLocal()
    try:
        return (
            db.query(Task)
            .filter(Task.chatid == chatid, Task.status == "pending")
            .order_by(Task.risk_score.desc())
            .first()
        )
    except Exception as e:
        print(f"[Scheduler] DB error getting top task: {e}")
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# LLM advice generation
# ---------------------------------------------------------------------------

ADVICE_PROMPT = """你是 DDL-Killer，一个大学生的智能学习规划助手。现在要给用户发一条任务提醒。

现在是 {now_str}，{weekday}。

用户最紧急的任务：
- 标题：{title}
- 截止时间：{ddl_str}
- 剩余时间：{hours_left}小时
- 难度：{difficulty}/10
- 重要度：{importance}/10
- 描述：{description}

请给出简短的个性化学习建议（3-5句话），要求：
1. 像朋友聊天一样自然，不要用「建议你...」开头
2. 根据剩余时间和难度，给出具体的阶段性安排（比如「今晚先搞定前30%」这种）
3. 如果剩余时间很短（<24h），语气要更紧迫，像催朋友赶 DDL
4. 如果剩余时间充裕（>3天），可以轻松一些，提醒别拖延
5. 结尾给一个具体的下一步行动（今晚做什么、明天做什么）
6. 不要重复任务标题和截止时间（消息面板已经展示了）
7. 不要用 markdown 格式，纯文本即可

直接输出建议内容，不要加任何前缀说明。"""


async def _generate_reminder(task: Task) -> str | None:
    """Call LLM to generate personalized reminder advice."""
    if not DEEPSEEK_API_KEY:
        return _fallback_advice(task)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    hours_left = max((task.ddl_time - now).total_seconds() / 3600, 0) if task.ddl_time else 72

    prompt = ADVICE_PROMPT.format(
        now_str=now.strftime("%Y-%m-%d %H:%M"),
        weekday=weekday,
        title=task.title,
        ddl_str=task.ddl_time.strftime("%m/%d %H:%M") if task.ddl_time else "未设定",
        hours_left=f"{hours_left:.1f}",
        difficulty=task.difficulty,
        importance=task.importance,
        description=task.description or "无",
    )

    client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,  # higher temp for more natural, varied advice
            ),
            timeout=LLM_TIMEOUT,
        )
        content = response.choices[0].message.content or ""
        return content.strip() if content.strip() else _fallback_advice(task)
    except Exception as e:
        print(f"[Scheduler] LLM error: {e}")
        return _fallback_advice(task)


def _fallback_advice(task: Task) -> str:
    """Template-based advice when LLM is unavailable."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hours_left = max((task.ddl_time - now).total_seconds() / 3600, 0) if task.ddl_time else 72

    if hours_left < 6:
        return "时间不多了，现在就开始动手吧！先搞定最核心的部分。"
    elif hours_left < 24:
        return "今天得抓紧了，建议先把最难的部分啃下来，别拖到最后一刻。"
    elif hours_left < 72:
        return "还有几天时间，可以分步骤来。今天先做个大概框架，后面细化。"
    else:
        return "时间还比较充裕，不过别等到最后一天。今天花一点时间先熟悉一下要求。"


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------


def _format_reminder_message(task: Task, advice: str) -> str:
    """Format the full reminder message with task info + LLM advice."""
    from app.services.display import format_reminder

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hours_left = max((task.ddl_time - now).total_seconds() / 3600, 0) if task.ddl_time else 0

    return format_reminder(task, advice, hours_left)
