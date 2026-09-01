"""Reportistica fiscale: profitto netto al netto delle tasse."""

from __future__ import annotations

import argparse
import csv
import os
import time
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from database import connect, ensure_db
from inventory import ensure_inventory_schema


@dataclass(frozen=True)
class TaxLine:
    listing_id: str
    title: str
    category: str
    bought_at: str
    sold_at: str
    buy_price_eur: float
    sold_price_eur: float
    gross_profit_eur: float
    taxable_base_eur: float
    tax_eur: float
    net_profit_eur: float


def _regime() -> str:
    return os.getenv("TAX_REGIME", "forfettario").strip().lower()


def _forfettario_rate() -> float:
    return float(os.getenv("TAX_FORFETTARIO_RATE", "0.15"))


def _forfettario_coeff() -> float:
    return float(os.getenv("TAX_FORFETTARIO_COEFF", "0.78"))


def _margine_iva_rate() -> float:
    return float(os.getenv("TAX_MARGINE_IVA_RATE", "0.22"))


def _period_bounds(year: int, month: int) -> tuple[float, float]:
    start = datetime(year, month, 1)
    last_day = monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59)
    return start.timestamp(), end.timestamp()


def _sold_rows_in_period(year: int, month: int) -> list[dict[str, Any]]:
    ensure_inventory_schema()
    start_ts, end_ts = _period_bounds(year, month)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT listing_id, title, category, brand, source,
                   buy_price_eur, sold_price_eur, bought_at, sold_at
            FROM inventory
            WHERE status = 'sold' AND sold_at >= ? AND sold_at <= ?
            ORDER BY sold_at ASC
            """,
            (start_ts, end_ts),
        ).fetchall()
    return [dict(row) for row in rows]


def _compute_tax(gross_profit: float, sold_price: float, buy_price: float) -> tuple[float, float, float]:
    regime = _regime()
    if gross_profit <= 0:
        return 0.0, 0.0, gross_profit

    if regime == "forfettario":
        taxable = sold_price * _forfettario_coeff()
        tax = taxable * _forfettario_rate()
        net = gross_profit - tax
        return taxable, tax, net

    if regime in ("margine", "margine_iva"):
        margin = max(0.0, sold_price - buy_price)
        taxable = margin / (1 + _margine_iva_rate())
        tax = margin - taxable
        net = gross_profit - tax
        return taxable, tax, net

    # ordinario: stima semplificata su profitto lordo
    tax_rate = float(os.getenv("TAX_ORDINARIO_RATE", "0.24"))
    tax = gross_profit * tax_rate
    return gross_profit, tax, gross_profit - tax


def build_tax_lines(year: int, month: int) -> list[TaxLine]:
    lines: list[TaxLine] = []
    for row in _sold_rows_in_period(year, month):
        buy = float(row["buy_price_eur"])
        sold = float(row["sold_price_eur"])
        gross = sold - buy
        taxable, tax, net = _compute_tax(gross, sold, buy)
        lines.append(
            TaxLine(
                listing_id=str(row["listing_id"]),
                title=str(row["title"]),
                category=str(row["category"]),
                bought_at=datetime.fromtimestamp(float(row["bought_at"])).strftime("%Y-%m-%d"),
                sold_at=datetime.fromtimestamp(float(row["sold_at"])).strftime("%Y-%m-%d"),
                buy_price_eur=buy,
                sold_price_eur=sold,
                gross_profit_eur=gross,
                taxable_base_eur=taxable,
                tax_eur=tax,
                net_profit_eur=net,
            )
        )
    return lines


def summarize_lines(lines: list[TaxLine]) -> dict[str, Any]:
    return {
        "n_sales": len(lines),
        "revenue_eur": sum(line.sold_price_eur for line in lines),
        "cost_eur": sum(line.buy_price_eur for line in lines),
        "gross_profit_eur": sum(line.gross_profit_eur for line in lines),
        "tax_eur": sum(line.tax_eur for line in lines),
        "net_profit_eur": sum(line.net_profit_eur for line in lines),
        "regime": _regime(),
    }


def export_tax_csv(year: int, month: int, *, out_dir: str | None = None) -> Path:
    lines = build_tax_lines(year, month)
    summary = summarize_lines(lines)
    out = Path(out_dir or os.getenv("TAX_REPORT_DIR", "data/reports"))
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"tax_report_{year}_{month:02d}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(
            [
                "listing_id",
                "titolo",
                "categoria",
                "acquisto",
                "vendita",
                "costo_eur",
                "ricavo_eur",
                "profitto_lordo_eur",
                "imponibile_eur",
                "tasse_eur",
                "profitto_netto_eur",
            ]
        )
        for line in lines:
            writer.writerow(
                [
                    line.listing_id,
                    line.title,
                    line.category,
                    line.bought_at,
                    line.sold_at,
                    f"{line.buy_price_eur:.2f}",
                    f"{line.sold_price_eur:.2f}",
                    f"{line.gross_profit_eur:.2f}",
                    f"{line.taxable_base_eur:.2f}",
                    f"{line.tax_eur:.2f}",
                    f"{line.net_profit_eur:.2f}",
                ]
            )
        writer.writerow([])
        writer.writerow(["RIEPILOGO", f"regime={summary['regime']}"])
        writer.writerow(["Vendite", summary["n_sales"]])
        writer.writerow(["Ricavi", f"{summary['revenue_eur']:.2f}"])
        writer.writerow(["Costi acquisto", f"{summary['cost_eur']:.2f}"])
        writer.writerow(["Profitto lordo", f"{summary['gross_profit_eur']:.2f}"])
        writer.writerow(["Tasse stimate", f"{summary['tax_eur']:.2f}"])
        writer.writerow(["Profitto netto", f"{summary['net_profit_eur']:.2f}"])
    return path


def format_tax_report_telegram(year: int, month: int) -> str:
    lines = build_tax_lines(year, month)
    summary = summarize_lines(lines)
    if not lines:
        return f"📊 <b>Tax report {month:02d}/{year}</b>\nNessuna vendita nel periodo."
    return (
        f"📊 <b>Tax report {month:02d}/{year}</b> ({summary['regime']})\n"
        f"Vendite: <b>{summary['n_sales']}</b>\n"
        f"Ricavi: {summary['revenue_eur']:.0f} €\n"
        f"Costi: {summary['cost_eur']:.0f} €\n"
        f"Profitto lordo: {summary['gross_profit_eur']:.0f} €\n"
        f"Tasse stimate: <b>{summary['tax_eur']:.0f} €</b>\n"
        f"Profitto netto: <b>{summary['net_profit_eur']:.0f} €</b>"
    )


def send_tax_report_document(token: str, chat_id: str, year: int, month: int) -> Path:
    import requests

    path = export_tax_csv(year, month)
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    with path.open("rb") as fh:
        response = requests.post(
            url,
            data={"chat_id": chat_id},
            files={"document": (path.name, fh, "text/csv")},
            timeout=60,
        )
    if response.status_code != 200:
        raise RuntimeError(f"sendDocument {response.status_code}: {response.text[:300]}")
    return path


def parse_period_arg(arg: str) -> tuple[int, int]:
    arg = arg.strip()
    if "-" in arg:
        year_s, month_s = arg.split("-", 1)
        return int(year_s), int(month_s)
    if "/" in arg:
        month_s, year_s = arg.split("/", 1)
        return int(year_s), int(month_s)
    raise ValueError("Formato periodo: YYYY-MM o MM/YYYY")


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    ensure_db()
    parser = argparse.ArgumentParser(description="Report fiscale vendite inventario.")
    parser.add_argument("period", nargs="?", help="YYYY-MM (default mese corrente)")
    args = parser.parse_args()
    if args.period:
        year, month = parse_period_arg(args.period)
    else:
        now = datetime.now()
        year, month = now.year, now.month
    path = export_tax_csv(year, month)
    summary = summarize_lines(build_tax_lines(year, month))
    print(f"[tax] {path} — {summary['n_sales']} vendite, netto {summary['net_profit_eur']:.0f} €")


if __name__ == "__main__":
    main()
