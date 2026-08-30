"""Cooldown scraping per siti rumorosi (tanti lotti visti, zero alert)."""

from __future__ import annotations

import json
import time
from pathlib import Path

COOLDOWN_FILE = Path(__file__).resolve().parent / ".site_cooldown.json"
NOISY_SOURCES = frozenset({"prezzishock", "antiebay"})
STREAK_LIMIT = 10
NORMAL_HOURS = 4
REDUCED_HOURS = 8


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
    if source not in NOISY_SOURCES:
        return False
    ts = now if now is not None else time.time()
    until = float(_load().get(source, {}).get("skip_until") or 0)
    return ts < until


def record_run(source: str, *, discarded: int, sent: int, now: float | None = None) -> None:
    if source not in NOISY_SOURCES:
        return
    ts = now if now is not None else time.time()
    data = _load()
    state = data.get(source) or {"discarded_streak": 0, "skip_until": 0, "interval_hours": NORMAL_HOURS}
    if sent > 0:
        state["discarded_streak"] = 0
        state["interval_hours"] = NORMAL_HOURS
    else:
        state["discarded_streak"] = int(state.get("discarded_streak") or 0) + discarded
        if state["discarded_streak"] >= STREAK_LIMIT:
            state["interval_hours"] = REDUCED_HOURS
            state["skip_until"] = ts + REDUCED_HOURS * 3600
            print(
                f"[{source}] 10+ lotti scartati: prossimo scrape tra {REDUCED_HOURS}h."
            )
    data[source] = state
    _save(data)
