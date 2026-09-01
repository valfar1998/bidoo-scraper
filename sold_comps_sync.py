"""Aggiornamento automatico comps da venduti eBay (API o scraping)."""

from __future__ import annotations

import argparse
import os
import time

from dotenv import load_dotenv

from comps import CompRow, is_stale, load_comps, save_comps, stdev_of
from http_fetch import SessionFetcher
from update_comps import DEFAULT_PRODUCTS, _ebay_sold, _merge, _products, _vinted_search

try:
    from sources.ebay_api import credentials as ebay_credentials
    from sources.ebay_api import search_auctions
except ImportError:
    ebay_credentials = lambda: ("", "")  # type: ignore[assignment]
    search_auctions = None  # type: ignore[assignment]


def _ebay_api_sold_prices(product: str) -> list[float]:
    if search_auctions is None:
        return []
    app_id, cert = ebay_credentials()
    if not app_id or not cert:
        return []
    try:
        items = search_auctions(product, limit=40, extra_filter="buyingOptions:{FIXED_PRICE}")
    except Exception as exc:
        print(f"  eBay API {product}: {exc}")
        return []
    prices: list[float] = []
    for item in items:
        price = item.get("price") or {}
        value = price.get("value")
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if 8 <= parsed <= 800:
            prices.append(parsed)
    return prices


def sync_products(
    *,
    force: bool = False,
    limit: int = 0,
    prefer_api: bool = True,
) -> int:
    load_dotenv()
    existing = {row.product: row for row in load_comps()}
    products = _products() or list(DEFAULT_PRODUCTS)
    if limit:
        products = products[:limit]
    delay = float(os.getenv("COMPS_DELAY_S", "1.4"))
    updated: list[CompRow] = []
    changed = 0

    with SessionFetcher() as fetcher:
        for product in products:
            old = existing.get(product)
            if old and not force and not is_stale(old):
                updated.append(old)
                print(f"  {product}: fresco, skip")
                continue
            print(f"  {product}: sync venduti…")
            ebay = []
            if prefer_api:
                ebay = _ebay_api_sold_prices(product)
            if len(ebay) < 3:
                ebay = _ebay_sold(fetcher, product)
            time.sleep(delay)
            vinted = _vinted_search(fetcher, product)
            time.sleep(delay)
            row = _merge(old, product, ebay, vinted)
            if row.avg_price_ebay <= 0 and row.avg_price_vinted <= 0:
                if old:
                    updated.append(old)
                    print("    nessun prezzo nuovo, tengo storico")
                else:
                    print("    nessun prezzo, skip")
                continue
            if not old or (
                abs(row.avg_price_ebay - old.avg_price_ebay) > 0.5
                or abs(row.avg_price_vinted - old.avg_price_vinted) > 0.5
                or abs(row.stdev - old.stdev) > 0.5
            ):
                changed += 1
            print(
                f"    eBay {row.avg_price_ebay:.0f} € (n={row.n_ebay}) · "
                f"Vinted {row.avg_price_vinted:.0f} € (n={row.n_vinted}) · "
                f"stdev {row.stdev:.0f}"
            )
            updated.append(row)

    kept = [row for key, row in existing.items() if key not in {item.product for item in updated}]
    save_comps(updated + kept)
    return changed


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Sync automatico comps venduti.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-api", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    changed = sync_products(force=args.force, limit=args.limit, prefer_api=not args.no_api)
    print(f"OK. Prodotti aggiornati: {changed}.")
    if not args.no_backup and os.getenv("BACKUP_AFTER_SYNC", "true").lower() in ("1", "true", "yes"):
        try:
            from db_backup import run_backup

            results = run_backup()
            for item in results:
                print(f"Backup → {item}")
        except Exception as exc:
            print(f"Backup fallito: {exc}")


if __name__ == "__main__":
    main()
