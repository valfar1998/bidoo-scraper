"""Invio notifiche Telegram."""

from __future__ import annotations

import html
from typing import Any

import requests

from dry_run import is_dry_run


def send_telegram_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
    message_thread_id: int | None = None,
) -> None:
    if is_dry_run():
        preview = text.replace("<b>", "").replace("</b>", "")[:120]
        print(f"[DRY_RUN] Telegram -> {chat_id}: {preview}…")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    if len(text) > 4096:
        text = text[:4090] + "…"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
    response = requests.post(url, json=payload, timeout=30)
    if response.status_code != 200:
        detail = response.text[:400]
        raise RuntimeError(
            f"Telegram {response.status_code}: {detail}. "
            "Spesso: caratteri HTML nel titolo (&, <) o chat_id errato."
        )
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body}")


def escape_html(text: str) -> str:
    return html.escape(text or "", quote=False)
