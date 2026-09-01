"""Proxy quality score e failover per dominio."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from database import connect, ensure_db


@dataclass(frozen=True)
class FetchRoute:
    strategy: str  # direct | proxy | proxy_alt | flaresolverr
    proxy_url: str | None
    reason: str = ""


def domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _ensure_table() -> None:
    ensure_db()
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_domain_stats (
                domain TEXT PRIMARY KEY,
                successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                blocked_403 INTEGER NOT NULL DEFAULT 0,
                blocked_429 INTEGER NOT NULL DEFAULT 0,
                avg_latency_ms REAL NOT NULL DEFAULT 0,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                preferred_strategy TEXT NOT NULL DEFAULT 'direct',
                last_status INTEGER,
                last_error TEXT,
                updated_at REAL NOT NULL DEFAULT 0
            )
            """
        )


def _row_for_domain(domain: str) -> dict[str, Any]:
    _ensure_table()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM proxy_domain_stats WHERE domain = ?",
            (domain,),
        ).fetchone()
    if not row:
        return {
            "domain": domain,
            "successes": 0,
            "failures": 0,
            "blocked_403": 0,
            "blocked_429": 0,
            "avg_latency_ms": 0.0,
            "consecutive_failures": 0,
            "preferred_strategy": "direct",
        }
    return dict(row)


def record_fetch(
    domain: str,
    *,
    latency_ms: float,
    status_code: int | None = None,
    blocked: bool = False,
    error: str = "",
) -> None:
    if not domain or os.getenv("PROXY_HEALTH", "true").lower() not in ("1", "true", "yes"):
        return
    _ensure_table()
    now = time.time()
    row = _row_for_domain(domain)
    successes = int(row["successes"])
    failures = int(row["failures"])
    blocked_403 = int(row["blocked_403"])
    blocked_429 = int(row["blocked_429"])
    consec = int(row["consecutive_failures"])
    avg_lat = float(row["avg_latency_ms"])
    strategy = str(row["preferred_strategy"])

    if blocked or (status_code and status_code >= 400):
        failures += 1
        consec += 1
        if status_code == 403:
            blocked_403 += 1
        if status_code == 429:
            blocked_429 += 1
        if consec >= 2:
            strategy = _escalate_strategy(strategy)
    else:
        successes += 1
        consec = 0
        avg_lat = (avg_lat * 0.7) + (latency_ms * 0.3) if avg_lat else latency_ms
        if strategy != "direct" and successes >= 3:
            strategy = _deescalate_strategy(strategy)

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO proxy_domain_stats (
                domain, successes, failures, blocked_403, blocked_429,
                avg_latency_ms, consecutive_failures, preferred_strategy,
                last_status, last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                successes = excluded.successes,
                failures = excluded.failures,
                blocked_403 = excluded.blocked_403,
                blocked_429 = excluded.blocked_429,
                avg_latency_ms = excluded.avg_latency_ms,
                consecutive_failures = excluded.consecutive_failures,
                preferred_strategy = excluded.preferred_strategy,
                last_status = excluded.last_status,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                domain,
                successes,
                failures,
                blocked_403,
                blocked_429,
                avg_lat,
                consec,
                strategy,
                status_code,
                error[:200],
                now,
            ),
        )


def _escalate_strategy(current: str) -> str:
    order = ["direct", "proxy", "proxy_alt", "flaresolverr"]
    try:
        idx = order.index(current)
    except ValueError:
        idx = 0
    return order[min(idx + 1, len(order) - 1)]


def _deescalate_strategy(current: str) -> str:
    order = ["direct", "proxy", "proxy_alt", "flaresolverr"]
    try:
        idx = order.index(current)
    except ValueError:
        return "direct"
    return order[max(idx - 1, 0)]


def _proxy_url(kind: str) -> str | None:
    if kind == "proxy":
        return (os.getenv("ROTATING_PROXY_URL") or "").strip() or None
    if kind == "proxy_alt":
        return (
            (os.getenv("ROTATING_PROXY_URL_ALT") or os.getenv("ROTATING_PROXY_URL") or "")
            .strip()
            or None
        )
    return None


def resolve_fetch_route(domain: str, *, force_flare: bool = False) -> FetchRoute:
    if os.getenv("PROXY_HEALTH", "true").lower() not in ("1", "true", "yes"):
        return FetchRoute(strategy="direct", proxy_url=None)

    row = _row_for_domain(domain)
    strategy = "flaresolverr" if force_flare else str(row["preferred_strategy"])
    proxy = _proxy_url(strategy) if strategy in ("proxy", "proxy_alt") else None
    if strategy in ("proxy", "proxy_alt") and not proxy:
        strategy = "flaresolverr"
        proxy = None

    reason = ""
    if int(row["consecutive_failures"]) >= 2:
        reason = f"{int(row['consecutive_failures'])} fail consecutivi"
    elif float(row["avg_latency_ms"]) > float(os.getenv("PROXY_SLOW_MS", "8000")):
        reason = f"latenza alta ({row['avg_latency_ms']:.0f}ms)"
        strategy = _escalate_strategy(strategy)
        proxy = _proxy_url(strategy)

    return FetchRoute(strategy=strategy, proxy_url=proxy, reason=reason)


def quality_summary() -> list[dict[str, Any]]:
    _ensure_table()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT domain, successes, failures, blocked_403, blocked_429,
                   avg_latency_ms, preferred_strategy, consecutive_failures, updated_at
            FROM proxy_domain_stats
            ORDER BY failures DESC, updated_at DESC
            LIMIT 20
            """
        ).fetchall()
    out = []
    for row in rows:
        total = int(row["successes"]) + int(row["failures"])
        score = (int(row["successes"]) / total * 100) if total else 100.0
        out.append(
            {
                "domain": row["domain"],
                "score_pct": round(score, 1),
                "strategy": row["preferred_strategy"],
                "latency_ms": round(float(row["avg_latency_ms"]), 0),
                "failures": int(row["failures"]),
                "blocked_403": int(row["blocked_403"]),
                "blocked_429": int(row["blocked_429"]),
            }
        )
    return out
