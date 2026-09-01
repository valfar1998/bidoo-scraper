"""Analisi velocità di puntata da auction_history."""

from __future__ import annotations

import os
from dataclasses import dataclass

from auction_history import TrackedAuction


@dataclass(frozen=True)
class VelocityResult:
    bid_events: int
    window_minutes: float
    bids_per_hour: float
    price_delta_cents: int
    is_hot: bool
    confidence_penalty: int
    note: str


def _window_seconds() -> int:
    try:
        return max(300, int(os.getenv("BIDDING_VELOCITY_WINDOW_MIN", "30")) * 60)
    except ValueError:
        return 1800


def _hot_threshold() -> float:
    try:
        return float(os.getenv("BIDDING_VELOCITY_HOT_PER_HOUR", "6"))
    except ValueError:
        return 6.0


def _penalty() -> int:
    try:
        return max(0, int(os.getenv("BIDDING_VELOCITY_CONFIDENCE_PENALTY", "20")))
    except ValueError:
        return 20


def analyze_velocity(
    tracked: TrackedAuction | None,
    *,
    remaining_seconds: int | None = None,
) -> VelocityResult:
    if tracked is None or tracked.observation_count < 2:
        return VelocityResult(0, 0.0, 0.0, 0, False, 0, "")

    window_s = _window_seconds()
    if remaining_seconds is not None and remaining_seconds > window_s:
        # Fuori finestra finale: nessuna penalità
        return VelocityResult(0, 0.0, 0.0, 0, False, 0, "")

    observations = tracked.observations
    if len(observations) < 2:
        return VelocityResult(0, 0.0, 0.0, 0, False, 0, "")

    latest_ts = observations[-1].ts
    window_start = latest_ts - window_s
    in_window = [obs for obs in observations if obs.ts >= window_start]
    if len(in_window) < 2:
        return VelocityResult(0, 0.0, 0.0, 0, False, 0, "")

    bid_events = 0
    for prev, curr in zip(in_window, in_window[1:]):
        if curr.price_cents > prev.price_cents:
            bid_events += 1
    span_s = max(60.0, in_window[-1].ts - in_window[0].ts)
    window_minutes = span_s / 60.0
    bids_per_hour = bid_events / span_s * 3600.0
    prices = [obs.price_cents for obs in in_window]
    price_delta = max(prices) - min(prices)
    hot = bids_per_hour >= _hot_threshold()
    penalty = _penalty() if hot else 0
    note = ""
    if hot:
        note = (
            f"Puntate accelerate ({bids_per_hour:.1f}/h negli ultimi "
            f"{window_minutes:.0f} min): competizione in chiusura"
        )
    return VelocityResult(
        bid_events=bid_events,
        window_minutes=window_minutes,
        bids_per_hour=bids_per_hour,
        price_delta_cents=price_delta,
        is_hot=hot,
        confidence_penalty=penalty,
        note=note,
    )
