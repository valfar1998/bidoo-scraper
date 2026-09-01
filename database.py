"""Database SQLite unico: stato, feedback, comps, metriche, cache."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from dry_run import dry_run_skip_write

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "monitor.db"

_LOCK = threading.Lock()


def db_enabled() -> bool:
    return os.getenv("USE_DATABASE", "true").lower() in ("1", "true", "yes")


def db_path() -> Path:
    raw = os.getenv("DATABASE_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_DB


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _LOCK:
        with connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS alert_state (
                    alert_key TEXT PRIMARY KEY,
                    last_alert_ts REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auction_history (
                    auction_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    slug TEXT NOT NULL DEFAULT '',
                    retail_value REAL NOT NULL DEFAULT 0,
                    url TEXT NOT NULL DEFAULT '',
                    category_tag TEXT NOT NULL DEFAULT '',
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    observations_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS feedback_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bucket TEXT NOT NULL,
                    listing_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    brand TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    ts REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_listing
                    ON feedback_events(listing_id);
                CREATE INDEX IF NOT EXISTS idx_feedback_bucket
                    ON feedback_events(bucket, ts);

                CREATE TABLE IF NOT EXISTS site_cooldown (
                    source TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS comps (
                    product TEXT PRIMARY KEY,
                    avg_price_ebay REAL NOT NULL DEFAULT 0,
                    avg_price_vinted REAL NOT NULL DEFAULT 0,
                    stdev REAL NOT NULL DEFAULT 0,
                    n_ebay INTEGER NOT NULL DEFAULT 0,
                    n_vinted INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS market_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    ts REAL NOT NULL,
                    fetched INTEGER NOT NULL DEFAULT 0,
                    discarded INTEGER NOT NULL DEFAULT 0,
                    sent INTEGER NOT NULL DEFAULT 0,
                    waf_blocked INTEGER NOT NULL DEFAULT 0,
                    theoretical_margin_eur REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_run_stats_source_ts
                    ON run_stats(source, ts);

                CREATE TABLE IF NOT EXISTS vision_cache (
                    listing_id TEXT PRIMARY KEY,
                    image_url TEXT NOT NULL DEFAULT '',
                    analysis_json TEXT NOT NULL,
                    ts REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            migrated = conn.execute(
                "SELECT value FROM meta WHERE key = 'migrated_v1'"
            ).fetchone()
            if not migrated:
                _migrate_legacy_files(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('migrated_v1', ?)",
                    (time.time(),),
                )


def _migrate_legacy_files(conn: sqlite3.Connection) -> None:
    alert_file = ROOT / ".alert_state.json"
    if alert_file.exists():
        try:
            data = json.loads(alert_file.read_text(encoding="utf-8"))
            for key, value in data.items():
                conn.execute(
                    "INSERT OR IGNORE INTO alert_state(alert_key, last_alert_ts) VALUES(?, ?)",
                    (str(key), float(value)),
                )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    history_file = ROOT / ".auction_history.json"
    if history_file.exists():
        try:
            raw = json.loads(history_file.read_text(encoding="utf-8"))
            for auction_id, payload in (raw.get("auctions") or {}).items():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO auction_history(
                        auction_id, name, slug, retail_value, url, category_tag,
                        first_seen, last_seen, observations_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(auction_id),
                        str(payload.get("name", "")),
                        str(payload.get("slug", "")),
                        float(payload.get("retail_value", 0)),
                        str(payload.get("url", "")),
                        str(payload.get("category_tag", "")),
                        float(payload.get("first_seen", 0)),
                        float(payload.get("last_seen", 0)),
                        json.dumps(payload.get("observations") or []),
                    ),
                )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    feedback_file = ROOT / ".feedback.json"
    if feedback_file.exists():
        try:
            raw = json.loads(feedback_file.read_text(encoding="utf-8"))
            for bucket in ("seen", "ignored", "bought", "sold", "estimate_error"):
                for item in raw.get(bucket) or []:
                    conn.execute(
                        """
                        INSERT INTO feedback_events(
                            bucket, listing_id, title, brand, category, source, ts
                        ) VALUES(?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            bucket,
                            str(item.get("id", "")),
                            str(item.get("title", "")),
                            str(item.get("brand", "")),
                            str(item.get("category", "")),
                            str(item.get("source", "")),
                            float(item.get("ts", time.time())),
                        ),
                    )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    cooldown_file = ROOT / ".site_cooldown.json"
    if cooldown_file.exists():
        try:
            raw = json.loads(cooldown_file.read_text(encoding="utf-8"))
            for source, state in raw.items():
                conn.execute(
                    "INSERT OR IGNORE INTO site_cooldown(source, state_json) VALUES(?, ?)",
                    (str(source), json.dumps(state)),
                )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    comps_file = ROOT / "data" / "comps.csv"
    if comps_file.exists():
        try:
            with comps_file.open(encoding="utf-8", newline="") as handle:
                for raw in csv.DictReader(handle):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO comps(
                            product, avg_price_ebay, avg_price_vinted, stdev,
                            n_ebay, n_vinted, updated_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(raw.get("product", "")).strip().lower(),
                            float(raw.get("avg_price_ebay") or 0),
                            float(raw.get("avg_price_vinted") or 0),
                            float(raw.get("stdev") or 0),
                            int(raw.get("n_ebay") or 0),
                            int(raw.get("n_vinted") or 0),
                            float(raw.get("updated_at") or 0),
                        ),
                    )
        except (TypeError, ValueError, OSError):
            pass

    cache_file = ROOT / ".market_cache.json"
    if cache_file.exists():
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            now = time.time()
            for key, payload in raw.items():
                expires = float(payload.get("expires_at") or 0)
                if expires <= now:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO market_cache(cache_key, payload_json, expires_at)
                    VALUES(?, ?, ?)
                    """,
                    (str(key), json.dumps(payload.get("data") or {}), expires),
                )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass


def ensure_db() -> None:
    if db_enabled():
        init_db()


# --- Alert state ---


def load_alert_state_map() -> dict[str, float]:
    ensure_db()
    if not db_enabled():
        return {}
    with connect() as conn:
        rows = conn.execute("SELECT alert_key, last_alert_ts FROM alert_state").fetchall()
    return {str(row["alert_key"]): float(row["last_alert_ts"]) for row in rows}


def save_alert_state_map(last_alert: dict[str, float]) -> None:
    ensure_db()
    if not db_enabled() or dry_run_skip_write("alert_state"):
        return
    with connect() as conn:
        conn.execute("DELETE FROM alert_state")
        conn.executemany(
            "INSERT INTO alert_state(alert_key, last_alert_ts) VALUES(?, ?)",
            [(str(key), float(value)) for key, value in last_alert.items()],
        )


# --- Run stats / health ---


@dataclass(frozen=True)
class RunStat:
    source: str
    ts: float
    fetched: int
    discarded: int
    sent: int
    waf_blocked: int
    theoretical_margin_eur: float


def record_run_stat(
    source: str,
    *,
    fetched: int = 0,
    discarded: int = 0,
    sent: int = 0,
    waf_blocked: int = 0,
    theoretical_margin_eur: float = 0.0,
    now: float | None = None,
) -> None:
    ensure_db()
    if not db_enabled() or dry_run_skip_write("run_stats"):
        return
    ts = now if now is not None else time.time()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO run_stats(
                source, ts, fetched, discarded, sent, waf_blocked, theoretical_margin_eur
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (source, ts, fetched, discarded, sent, waf_blocked, theoretical_margin_eur),
        )


def run_stats_since(since_ts: float) -> list[RunStat]:
    ensure_db()
    if not db_enabled():
        return []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT source, ts, fetched, discarded, sent, waf_blocked, theoretical_margin_eur
            FROM run_stats
            WHERE ts >= ?
            ORDER BY ts ASC
            """,
            (since_ts,),
        ).fetchall()
    return [
        RunStat(
            source=str(row["source"]),
            ts=float(row["ts"]),
            fetched=int(row["fetched"]),
            discarded=int(row["discarded"]),
            sent=int(row["sent"]),
            waf_blocked=int(row["waf_blocked"]),
            theoretical_margin_eur=float(row["theoretical_margin_eur"]),
        )
        for row in rows
    ]


# --- Vision cache ---


def get_vision_cache(listing_id: str) -> dict[str, Any] | None:
    ensure_db()
    if not db_enabled():
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT analysis_json, image_url, ts FROM vision_cache WHERE listing_id = ?",
            (listing_id,),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["analysis_json"])
    except json.JSONDecodeError:
        return None
    data["_image_url"] = row["image_url"]
    data["_ts"] = float(row["ts"])
    return data


def save_vision_cache(listing_id: str, image_url: str, analysis: dict[str, Any]) -> None:
    ensure_db()
    if not db_enabled():
        return
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO vision_cache(listing_id, image_url, analysis_json, ts)
            VALUES(?, ?, ?, ?)
            """,
            (listing_id, image_url, json.dumps(analysis), time.time()),
        )
