#!/usr/bin/env python3
"""Lancia i monitor configurati (Bidoo + aste classiche)."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from classic_monitor import enabled_sources, run_source
from site_profiles import PROFILES


def main() -> None:
    load_dotenv()
    sources = enabled_sources()
    include_bidoo = os.getenv("INCLUDE_BIDOO", "true").lower() in ("1", "true", "yes")
    print("Fonti:", ", ".join((["bidoo"] if include_bidoo else []) + sources))

    errors = 0
    if include_bidoo:
        try:
            from monitor import load_settings, run_check, load_alert_state
            from auction_history import AuctionHistory

            settings = load_settings()
            sent = run_check(settings, load_alert_state(), AuctionHistory())
            print(f"[bidoo] Alert inviati: {sent}.")
        except Exception as exc:
            errors += 1
            print(f"[bidoo] Errore: {exc}", file=sys.stderr)

    for source in sources:
        if source == "bidoo":
            continue
        if source not in PROFILES:
            print(f"Sito sconosciuto: {source}", file=sys.stderr)
            continue
        try:
            run_source(source)
        except Exception as exc:
            errors += 1
            print(f"[{source}] Errore: {exc}", file=sys.stderr)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
