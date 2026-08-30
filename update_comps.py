"""Aggiorna data/comps.csv da eBay (venduti) e Vinted (ricerca). Scraping leggero, ogni 7 giorni."""

from __future__ import annotations

import argparse
import os
import re
import time
from urllib.parse import quote_plus

from dotenv import load_dotenv

from comps import CompRow, COMPS_FILE, is_stale, load_comps, save_comps, stdev_of
from http_fetch import BROWSER_HEADERS, SessionFetcher
from money import parse_euro

DEFAULT_PRODUCTS = (
    "casio",
    "g-shock",
    "garmin",
    "xiaomi",
    "makita",
    "bosch",
    "dewalt",
    "kenwood",
    "philips",
    "rowenta",
    "nike",
    "adidas",
    "vans",
    "puma",
    "lego",
    "chicco",
    "gopro",
    "dji",
    "nintendo",
    "seiko",
    "fossil",
    "ninja",
    "karcher",
    "milwaukee",
    "kindle",
    "sandisk",
    "bialetti",
    "nespresso",
    "furla",
    "ray-ban",
)

_PRICE_RE = re.compile(
    r"(?:EUR|€)\s*([\d.]{1,6})(?:[.,](\d{2}))?",
    re.I,
)


def _products() -> list[str]:
    raw = os.getenv("COMPS_PRODUCTS", "")
    if raw.strip():
        return [item.strip().lower() for item in raw.split(",") if item.strip()]
    return list(DEFAULT_PRODUCTS)


def _extract_prices(html: str, *, lo: float = 8.0, hi: float = 800.0) -> list[float]:
    values: list[float] = []
    for match in _PRICE_RE.finditer(html):
        parsed = parse_euro(match.group(0))
        if parsed is None:
            continue
        if lo <= parsed <= hi:
            values.append(parsed)
    # Tieni il cuore della distribuzione (scarta outlier estremi).
    values.sort()
    if len(values) >= 8:
        cut = max(1, len(values) // 10)
        values = values[cut:-cut]
    return values[:40]


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _ebay_sold(fetcher: SessionFetcher, product: str) -> list[float]:
    url = (
        "https://www.ebay.it/sch/i.html?_nkw="
        f"{quote_plus(product)}&LH_Complete=1&LH_Sold=1&rt=nc&_ipg=60"
    )
    try:
        html = fetcher.get_text(url)
    except Exception as exc:
        print(f"  eBay {product}: {exc}")
        return []
    return _extract_prices(html)


def _vinted_search(fetcher: SessionFetcher, product: str) -> list[float]:
    url = f"https://www.vinted.it/catalog?search_text={quote_plus(product)}&order=newest_first"
    try:
        html = fetcher.get_text(url)
    except Exception as exc:
        print(f"  Vinted {product}: {exc}")
        return []
    return _extract_prices(html, lo=8.0, hi=400.0)


def _merge(old: CompRow | None, product: str, ebay: list[float], vinted: list[float]) -> CompRow:
    if len(ebay) < 3 and old and old.avg_price_ebay > 0:
        ebay_avg, ebay_n = old.avg_price_ebay, old.n_ebay
        ebay_stdev = old.stdev
    else:
        ebay_avg, ebay_n = _avg(ebay), len(ebay)
        ebay_stdev = stdev_of(ebay)
    if len(vinted) < 3 and old and old.avg_price_vinted > 0:
        vinted_avg, vinted_n = old.avg_price_vinted, old.n_vinted
    else:
        vinted_avg, vinted_n = _avg(vinted), len(vinted)
        ebay_stdev = max(ebay_stdev, stdev_of(vinted)) if vinted else ebay_stdev
    if old and ebay_n == 0 and vinted_n == 0:
        return old
    if ebay_n == 0 and vinted_n == 0:
        return old or CompRow(product, 0, 0, 0, 0, 0, 0)
    all_prices = ebay + vinted
    stdev = stdev_of(all_prices) if len(all_prices) >= 2 else ebay_stdev
    return CompRow(
        product=product,
        avg_price_ebay=ebay_avg,
        avg_price_vinted=vinted_avg,
        stdev=stdev,
        n_ebay=ebay_n,
        n_vinted=vinted_n,
        updated_at=time.time(),
    )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Aggiorna comps eBay/Vinted (ogni 7 giorni).")
    parser.add_argument("--force", action="store_true", help="Riscrapa anche se il CSV è fresco")
    parser.add_argument("--limit", type=int, default=0, help="Max prodotti da aggiornare")
    args = parser.parse_args()

    existing = {row.product: row for row in load_comps()}
    products = _products()
    if args.limit:
        products = products[: args.limit]
    delay = float(os.getenv("COMPS_DELAY_S", "1.4"))

    print(f"Comps → {COMPS_FILE}")
    with SessionFetcher() as fetcher:
        updated: list[CompRow] = []
        for product in products:
            old = existing.get(product)
            if old and not args.force and not is_stale(old):
                updated.append(old)
                print(f"  {product}: fresco, skip")
                continue
            print(f"  {product}: scrape eBay venduti + Vinted…")
            ebay = _ebay_sold(fetcher, product)
            time.sleep(delay)
            vinted = _vinted_search(fetcher, product)
            time.sleep(delay)
            row = _merge(old, product, ebay, vinted)
            if row.avg_price_ebay <= 0 and row.avg_price_vinted <= 0:
                if old:
                    updated.append(old)
                    print("    nessun prezzo nuovo, tengo il CSV")
                else:
                    print("    nessun prezzo, skip")
                continue
            print(
                f"    eBay {row.avg_price_ebay:.0f} € (n={row.n_ebay}) · "
                f"Vinted {row.avg_price_vinted:.0f} € (n={row.n_vinted}) · "
                f"stdev {row.stdev:.0f}"
            )
            updated.append(row)
        # Conserva prodotti nel CSV che non sono in questa lista.
        kept = [row for key, row in existing.items() if key not in {item.product for item in updated}]
        save_comps(updated + kept)
    print("OK.")


if __name__ == "__main__":
    main()
