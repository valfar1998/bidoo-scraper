"""Alerting su fallimenti scraper consecutivi (Telegram Dev)."""

from __future__ import annotations

import os
import time

from database import connect, ensure_db
from telegram_notifier import send_telegram_message


def _threshold() -> int:
    try:
        return max(1, int(os.getenv("SCRAPER_FAIL_ALERT_THRESHOLD", "3")))
    except ValueError:
        return 3


def _dev_chat_id() -> str:
    return os.getenv("TELEGRAM_DEV_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "")).strip()


def _get_streak(source: str) -> int:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (f"fail_streak:{source}",)
        ).fetchone()
    if not row:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def _set_meta(key: str, value: str) -> None:
    ensure_db()
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            (key, value),
        )


def record_scraper_success(source: str) -> None:
    _set_meta(f"fail_streak:{source}", "0")


def record_scraper_failure(source: str, error: str, *, now: float | None = None) -> bool:
    """Registra fallimento. Ritorna True se è stato inviato alert dev."""
    ts = now if now is not None else time.time()
    streak = _get_streak(source) + 1
    _set_meta(f"fail_streak:{source}", str(streak))
    _set_meta(f"fail_last_error:{source}", error[:500])
    _set_meta(f"fail_last_ts:{source}", str(ts))
    threshold = _threshold()
    if streak < threshold:
        return False
    if streak > threshold:
        # Alert solo al raggiungimento esatto della soglia, poi ogni N ulteriori
        if (streak - threshold) % threshold != 0:
            return False
    return _send_dev_alert(source, error, streak)


def _send_dev_alert(source: str, error: str, streak: int) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = _dev_chat_id()
    if not token or not chat_id:
        print(f"[health] {source}: {streak} fallimenti — {error[:120]} (no TELEGRAM_DEV_CHAT_ID)")
        return False
    text = (
        f"🚨 <b>Dev alert — scraper</b>\n\n"
        f"Fonte: <b>{source}</b>\n"
        f"Fallimenti consecutivi: <b>{streak}</b>\n"
        f"Errore: <code>{error[:400]}</code>\n\n"
        f"Verifica layout sito, proxy o WAF."
    )
    try:
        send_telegram_message(token, chat_id, text)
        return True
    except Exception as exc:
        print(f"[health] Invio alert fallito: {exc}")
        return False


def failure_summary() -> dict[str, dict[str, str | int]]:
    ensure_db()
    summary: dict[str, dict[str, str | int]] = {}
    with connect() as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE 'fail_streak:%'"
        ).fetchall()
    for row in rows:
        key = str(row["key"])
        source = key.split(":", 1)[1]
        streak = int(row["value"] or 0)
        if streak <= 0:
            continue
        with connect() as conn:
            err_row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (f"fail_last_error:{source}",)
            ).fetchone()
        summary[source] = {
            "streak": streak,
            "error": str(err_row["value"]) if err_row else "",
        }
    return summary
