"""Mini-database locale di prezzi medi eBay/Vinted (CSV)."""

from __future__ import annotations

import csv
import math
import time
from dataclasses import dataclass
from pathlib import Path

from brands import find_brand

COMPS_FILE = Path(__file__).resolve().parent / "data" / "comps.csv"
STALE_SECONDS = 7 * 24 * 3600
MIN_AVG_EUR = 15.0
MAX_STDEV_RATIO = 0.40


@dataclass(frozen=True)
class CompRow:
    product: str
    avg_price_ebay: float
    avg_price_vinted: float
    stdev: float
    n_ebay: int
    n_vinted: int
    updated_at: float

    @property
    def best_avg(self) -> float:
        values = [value for value in (self.avg_price_ebay, self.avg_price_vinted) if value > 0]
        return max(values) if values else 0.0

    @property
    def too_volatile(self) -> bool:
        avg = self.best_avg
        if avg <= 0 or self.stdev <= 0:
            return False
        return (self.stdev / avg) > MAX_STDEV_RATIO

    @property
    def too_cheap(self) -> bool:
        avg = self.best_avg
        return 0 < avg < MIN_AVG_EUR

    @property
    def reliable(self) -> bool:
        avg = self.best_avg
        if avg <= 0 or self.stdev <= 0:
            return False
        return (self.stdev / avg) < 0.25 and not self.too_cheap

    @property
    def super_reliable(self) -> bool:
        avg = self.best_avg
        if avg <= 25 or self.stdev <= 0:
            return False
        return (self.stdev / avg) < 0.15 and not self.too_cheap


def load_comps(path: Path = COMPS_FILE) -> list[CompRow]:
    if not path.exists():
        return []
    rows: list[CompRow] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            try:
                rows.append(
                    CompRow(
                        product=str(raw.get("product", "")).strip().lower(),
                        avg_price_ebay=float(raw.get("avg_price_ebay") or 0),
                        avg_price_vinted=float(raw.get("avg_price_vinted") or 0),
                        stdev=float(raw.get("stdev") or 0),
                        n_ebay=int(raw.get("n_ebay") or 0),
                        n_vinted=int(raw.get("n_vinted") or 0),
                        updated_at=float(raw.get("updated_at") or 0),
                    )
                )
            except (TypeError, ValueError):
                continue
    return rows


def save_comps(rows: list[CompRow], path: Path = COMPS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "product",
                "avg_price_ebay",
                "avg_price_vinted",
                "stdev",
                "n_ebay",
                "n_vinted",
                "updated_at",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "product": row.product,
                    "avg_price_ebay": f"{row.avg_price_ebay:.2f}",
                    "avg_price_vinted": f"{row.avg_price_vinted:.2f}",
                    "stdev": f"{row.stdev:.2f}",
                    "n_ebay": row.n_ebay,
                    "n_vinted": row.n_vinted,
                    "updated_at": int(row.updated_at),
                }
            )


def match_comp(title: str, rows: list[CompRow] | None = None) -> CompRow | None:
    rows = rows if rows is not None else load_comps()
    text = title.lower()
    brand = find_brand(title)
    ranked: list[tuple[int, CompRow]] = []
    for row in rows:
        if not row.product:
            continue
        if row.product in text:
            ranked.append((len(row.product), row))
            continue
        if brand and brand == row.product:
            ranked.append((len(brand), row))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def match_brand_comp(brand: str | None, rows: list[CompRow] | None = None) -> CompRow | None:
    if not brand:
        return None
    rows = rows if rows is not None else load_comps()
    needle = brand.lower()
    ebay: list[float] = []
    vinted: list[float] = []
    stdevs: list[float] = []
    for row in rows:
        if needle not in row.product:
            continue
        if row.avg_price_ebay > 0:
            ebay.append(row.avg_price_ebay)
        if row.avg_price_vinted > 0:
            vinted.append(row.avg_price_vinted)
        if row.stdev > 0:
            stdevs.append(row.stdev)
    if not ebay and not vinted:
        return None
    return CompRow(
        product=f"brand:{needle}",
        avg_price_ebay=sum(ebay) / len(ebay) if ebay else 0.0,
        avg_price_vinted=sum(vinted) / len(vinted) if vinted else 0.0,
        stdev=sum(stdevs) / len(stdevs) if stdevs else 0.0,
        n_ebay=len(ebay),
        n_vinted=len(vinted),
        updated_at=0.0,
    )


def stdev_of(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def is_stale(row: CompRow, now: float | None = None) -> bool:
    ts = now if now is not None else time.time()
    return (ts - row.updated_at) > STALE_SECONDS
