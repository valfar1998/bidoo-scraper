"""Modalità dry-run: simula pipeline senza Telegram né scritture DB prod."""

from __future__ import annotations

import os


def is_dry_run() -> bool:
    return os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")


def dry_run_banner(component: str) -> None:
    if is_dry_run():
        print(f"[DRY_RUN] {component}: nessun Telegram, nessuna scrittura DB produzione.")


def dry_run_skip_write(action: str, *, count: int = 1) -> bool:
    """Ritorna True se la scrittura va saltata (dry-run attivo)."""
    if not is_dry_run():
        return False
    suffix = f" ({count})" if count != 1 else ""
    print(f"[DRY_RUN] skip {action}{suffix}")
    return True
