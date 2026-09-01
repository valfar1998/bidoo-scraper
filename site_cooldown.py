"""Cooldown scraping: siti rumorosi, 0 alert per 3 giorni, 2 alert in 24h."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from database import connect, db_enabled, ensure_db
from dry_run import dry_run_skip_write

COOLDOWN_FILE = Path(__file__).resolve().parent / ".site_cooldown.json"
STREAK_LIMIT = 10
FAST_HOURS = 2
NORMAL_HOURS = 4
REDUCED_HOURS = 8
SLOW_HOURS = 24
ZERO_ALERT_DAYS = 7
HOT_ALERTS_48H = 3


def scrape_cooldown_hours() -> float:
    """Ore di pausa dopo troppi scarti; allineare a MAX_HOURS_TO_END."""
    try:
        return float(os.getenv("SCRAPE_COOLDOWN_HOURS", str(REDUCED_HOURS)))
    except ValueError:
        return float(REDUCED_HOURS)


def _load(path: Path = COOLDOWN_FILE) -> dict:
    ensure_db()
    if db_enabled():
        data: dict = {}
        with connect() as conn:
            rows = conn.execute("SELECT source, state_json FROM site_cooldown").fetchall()
        for row in rows:
            try:
                data[str(row["source"])] = json.loads(row["state_json"])
            except json.JSONDecodeError:
                continue
        return data
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict, path: Path = COOLDOWN_FILE) -> None:
    if db_enabled():
        with connect() as conn:
            conn.execute("DELETE FROM site_cooldown")
            for source, state in data.items():
                conn.execute(
                    "INSERT INTO site_cooldown(source, state_json) VALUES(?, ?)",
                    (str(source), json.dumps(state)),
                )
        return
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def should_skip(source: str, now: float | None = None) -> bool:
    ts = now if now is not None else time.time()
    until = float(_load().get(source, {}).get("skip_until") or 0)
    return ts < until


def remaining_skip_hours(source: str, now: float | None = None) -> float:
    ts = now if now is not None else time.time()
    until = float(_load().get(source, {}).get("skip_until") or 0)
    if until <= ts:
        return 0.0
    return (until - ts) / 3600


def record_run(source: str, *, discarded: int, sent: int, now: float | None = None) -> None:
    if dry_run_skip_write("site_cooldown"):
        return
    ts = now if now is not None else time.time()
    day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    data = _load()
    state = data.get(source) or {
        "discarded_streak": 0,
        "skip_until": 0,
        "interval_hours": NORMAL_HOURS,
        "alert_ts": [],
        "days_without": 0,
        "last_day": "",
    }
    events = [float(item) for item in (state.get("alert_ts") or []) if ts - float(item) < 172800]
    if sent > 0:
        events.extend([ts] * sent)
        state["discarded_streak"] = 0
        state["days_without"] = 0
        state["last_day"] = day
        if len(events) >= HOT_ALERTS_48H:
            state["interval_hours"] = FAST_HOURS
            state["skip_until"] = 0
            print(f"[{source}] {len(events)} alert in 48h: fetch più frequente (~{FAST_HOURS}h).")
        else:
            state["interval_hours"] = NORMAL_HOURS
            state["skip_until"] = 0
    else:
        state["discarded_streak"] = int(state.get("discarded_streak") or 0) + discarded
        last_day = str(state.get("last_day") or "")
        if last_day != day:
            if last_day:
                state["days_without"] = int(state.get("days_without") or 0) + 1
            state["last_day"] = day
        if int(state.get("days_without") or 0) >= ZERO_ALERT_DAYS:
            state["interval_hours"] = SLOW_HOURS
            state["skip_until"] = ts + SLOW_HOURS * 3600
            print(
                f"[{source}] 0 alert per {ZERO_ALERT_DAYS} giorni: fetch ridotto "
                f"(prossimo scrape ~{SLOW_HOURS}h)."
            )
        elif state["discarded_streak"] >= STREAK_LIMIT:
            pause_h = scrape_cooldown_hours()
            state["interval_hours"] = int(pause_h)
            state["skip_until"] = ts + pause_h * 3600
            print(
                f"[{source}] 10+ lotti scartati: prossimo scrape tra {pause_h:g}h."
            )
    state["alert_ts"] = events[-50:]
    data[source] = state
    _save(data)


def set_manual_pause(source: str, hours: float, *, now: float | None = None) -> float:
    """Pausa manuale (es. comando Telegram /pause). Ritorna ore impostate."""
    ts = now if now is not None else time.time()
    hours = max(0.5, float(hours))
    data = _load()
    state = data.get(source) or {
        "discarded_streak": 0,
        "skip_until": 0,
        "interval_hours": NORMAL_HOURS,
        "alert_ts": [],
        "days_without": 0,
        "last_day": "",
    }
    state["skip_until"] = ts + hours * 3600
    state["manual_pause"] = True
    data[source] = state
    _save(data)
    return hours
