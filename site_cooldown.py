"""Cooldown scraping: siti rumorosi, 0 alert per 3 giorni, 2 alert in 24h."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

COOLDOWN_FILE = Path(__file__).resolve().parent / ".site_cooldown.json"
STREAK_LIMIT = 10
FAST_HOURS = 2
NORMAL_HOURS = 4
REDUCED_HOURS = 8
SLOW_HOURS = 24
ZERO_ALERT_DAYS = 3
HOT_ALERTS_24H = 2


def _load(path: Path = COOLDOWN_FILE) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict, path: Path = COOLDOWN_FILE) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def should_skip(source: str, now: float | None = None) -> bool:
    ts = now if now is not None else time.time()
    until = float(_load().get(source, {}).get("skip_until") or 0)
    return ts < until


def record_run(source: str, *, discarded: int, sent: int, now: float | None = None) -> None:
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
    events = [float(item) for item in (state.get("alert_ts") or []) if ts - float(item) < 86400]
    if sent > 0:
        events.extend([ts] * sent)
        state["discarded_streak"] = 0
        state["days_without"] = 0
        state["last_day"] = day
        if len(events) >= HOT_ALERTS_24H:
            state["interval_hours"] = FAST_HOURS
            state["skip_until"] = 0
            print(f"[{source}] {len(events)} alert in 24h: fetch più frequente (~{FAST_HOURS}h).")
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
            state["interval_hours"] = REDUCED_HOURS
            state["skip_until"] = ts + REDUCED_HOURS * 3600
            print(
                f"[{source}] 10+ lotti scartati: prossimo scrape tra {REDUCED_HOURS}h."
            )
    state["alert_ts"] = events[-50:]
    data[source] = state
    _save(data)
