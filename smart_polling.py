"""Smart polling: discovery (lento) + sniper watch (lotti in chiusura)."""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from catalog_store import listings_closing_within, prune_stale, upsert_many
from classic_monitor import enabled_sources, run_source
from dry_run import dry_run_banner
from http_fetch import open_fetcher
from site_profiles import PROFILES
from sources import fetch_source


def discovery_hours() -> float:
    return float(os.getenv("DISCOVERY_INTERVAL_HOURS", "2"))


def sniper_window_hours() -> float:
    return float(os.getenv("SNIPER_WINDOW_HOURS", "2"))


def run_discovery(source: str) -> int:
    """Scansiona catalogo completo e salva nel DB (no alert)."""
    dry_run_banner(f"discovery:{source}")
    if source not in PROFILES:
        raise ValueError(f"Fonte sconosciuta: {source}")
    with open_fetcher() as fetcher:
        listings = fetch_source(source, fetcher)
    count = upsert_many(listings)
    pruned = prune_stale(max_age_hours=float(os.getenv("CATALOG_MAX_AGE_HOURS", "168")))
    print(f"[discovery:{source}] Salvati {count} lotti · rimossi {pruned} obsoleti.")
    return count


def run_sniper(source: str) -> int:
    """Monitora solo lotti in chiusura (alert + velocity)."""
    dry_run_banner(f"sniper:{source}")
    return run_source(source, mode="sniper")


def run_all_discovery() -> None:
    for source in enabled_sources():
        if source not in PROFILES:
            continue
        try:
            run_discovery(source)
        except Exception as exc:
            print(f"[discovery:{source}] Errore: {exc}", file=sys.stderr)


def run_all_sniper() -> None:
    errors = 0
    for source in enabled_sources():
        if source not in PROFILES:
            continue
        try:
            run_sniper(source)
        except Exception as exc:
            errors += 1
            print(f"[sniper:{source}] Errore: {exc}", file=sys.stderr)
    if errors:
        sys.exit(1)


def main() -> None:
    load_dotenv()
    dry_run_banner("smart_polling")
    parser = argparse.ArgumentParser(description="Smart polling discovery/sniper.")
    parser.add_argument("mode", choices=("discovery", "sniper"), help="Tipo job")
    parser.add_argument("--source", default="", help="Una fonte (default: tutte ENABLED_SOURCES)")
    args = parser.parse_args()
    if args.source:
        if args.mode == "discovery":
            run_discovery(args.source)
        else:
            run_sniper(args.source)
        return
    if args.mode == "discovery":
        run_all_discovery()
    else:
        run_all_sniper()


if __name__ == "__main__":
    main()
