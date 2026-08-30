"""Segna un lotto come ignorato / comprato / venduto. Dopo 20 lotti i filtri si adattano."""

from __future__ import annotations

import argparse
import sys

from brands import find_brand
from feedback import ADAPT_AFTER, FeedbackStore
from flip_rules import infer_flip_tag


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Segna un lotto (id = source:id come negli alert)."
    )
    parser.add_argument("action", choices=("ignored", "bought", "sold"))
    parser.add_argument("--id", required=True, help="es. remundo:123456 o prezzishock:abc")
    parser.add_argument("--title", default="", help="Titolo (se omesso usa lo storico)")
    args = parser.parse_args()

    store = FeedbackStore.load()
    listing_id = args.id.strip()
    previous = store.find(listing_id)
    title = args.title.strip() or (previous.get("title") if previous else "")
    if not title:
        print(
            "Titolo sconosciuto: passa --title \"...\" (serve per marca/categoria).",
            file=sys.stderr,
        )
        sys.exit(1)
    source = listing_id.split(":", 1)[0] if ":" in listing_id else ""
    brand = find_brand(title) or (previous.get("brand") if previous else None)
    category = (previous.get("category") if previous else "") or infer_flip_tag(title)
    store.record(
        args.action,
        listing_id=listing_id,
        title=title,
        brand=brand,
        category=category,
        source=source,
    )
    store.save()
    seen = len(store.seen)
    extra = (
        f"Filtri adattivi ATTIVI ({seen} lotti visti)."
        if store.adapted()
        else f"Ancora {max(0, ADAPT_AFTER - seen)} lotti visti prima dei filtri adattivi."
    )
    print(f"OK {args.action}: {listing_id} · marca={brand or '-'} · cat={category}")
    print(extra)


if __name__ == "__main__":
    main()
