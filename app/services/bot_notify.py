"""Send proactive messages via Enterprise WeChat bot webhook.

Used for scheduled reminders and notifications — not callbacks.
Requires the bot's webhook URL configured in .env as WECOM_WEBHOOK_URL.
"""

import json
import os

import requests

WECOM_WEBHOOK_URL = os.getenv("WECOM_WEBHOOK_URL", "")


def send_markdown(content: str) -> bool:
    """Send a markdown message to the bot's group chat."""
    if not WECOM_WEBHOOK_URL:
        return False

    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content},
    }

    try:
        resp = requests.post(WECOM_WEBHOOK_URL, json=payload, timeout=10)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def send_text(content: str, mentioned_list: list[str] | None = None) -> bool:
    """Send a plain text message, optionally @ mentioning users."""
    if not WECOM_WEBHOOK_URL:
        return False

    payload = {
        "msgtype": "text",
        "text": {
            "content": content,
            "mentioned_list": mentioned_list or [],
        },
    }

    try:
        resp = requests.post(WECOM_WEBHOOK_URL, json=payload, timeout=10)
        return resp.status_code == 200
    except requests.RequestException:
        return False
