"""Scomposizione manifest lotti multiprodotto con routing canale vendita."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from typing import Any, Literal

Channel = Literal["ebay", "vinted", "subito"]


@dataclass
class UnbundledItem:
    name: str
    category: str
    platform: Channel
    price_fast_eur: float
    price_max_eur: float
    notes: str = ""


@dataclass
class UnbundleResult:
    items: list[UnbundledItem] = field(default_factory=list)
    total_fast_eur: float = 0.0
    total_max_eur: float = 0.0
    source: str = "heuristic"


def _gemini_key() -> str:
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def _route_channel(name: str, category: str) -> Channel:
    text = f"{name} {category}".lower()
    bulky = ("divano", "mobile", "scrivania", "frigorif", "lavatrice", "pallet", "ingombr")
    tech = ("iphone", "samsung", "laptop", "pc ", "tablet", "ricamb", "scheda", "elettron")
    fashion = ("nike", "adidas", "abbigliamento", "scarpe", "borsa", "vestito", "camicia")

    if any(k in text for k in bulky):
        return "subito"
    if any(k in text for k in tech):
        return "ebay"
    if any(k in text for k in fashion):
        return "vinted"
    if "elettron" in text or category.lower() in ("elettronica", "smartwatch"):
        return "ebay"
    if category.lower() in ("abbigliamento", "scarpe"):
        return "vinted"
    return "ebay"


def _parse_manifest_line(line: str) -> tuple[str, float] | None:
    line = line.strip()
    if not line or len(line) < 3:
        return None
    qty = 1
    name = line
    price = 0.0

    qty_match = re.match(r"^(\d+)\s*x\s*(.+)$", line, re.I)
    if qty_match:
        qty = int(qty_match.group(1))
        name = qty_match.group(2).strip()

    price_match = re.search(r"(\d+[.,]\d+|\d+)\s*(?:€|eur)\b", name, re.I)
    if price_match:
        price = float(price_match.group(1).replace(",", "."))
        name = name[: price_match.start()].strip(" -–@:|")

    if not name:
        return None
    return name, price * qty


def _heuristic_unbundle(
    manifest_lines: list[str],
    title: str,
    total_retail_eur: float = 0.0,
) -> UnbundleResult:
    items: list[UnbundledItem] = []
    for raw in manifest_lines:
        parsed = _parse_manifest_line(raw)
        if not parsed:
            continue
        name, retail = parsed
        cat = "elettronica" if any(
            k in name.lower() for k in ("iphone", "samsung", "laptop", "tablet")
        ) else "varie"
        platform = _route_channel(name, cat)
        base = retail if retail > 0 else (total_retail_eur / max(len(manifest_lines), 1))
        fast = base * 0.55
        max_p = base * 0.75
        items.append(
            UnbundledItem(
                name=name[:120],
                category=cat,
                platform=platform,
                price_fast_eur=round(fast, 2),
                price_max_eur=round(max_p, 2),
            )
        )
    if not items and title:
        items.append(
            UnbundledItem(
                name=title[:120],
                category="varie",
                platform=_route_channel(title, "varie"),
                price_fast_eur=round(total_retail_eur * 0.5, 2),
                price_max_eur=round(total_retail_eur * 0.7, 2),
            )
        )
    total_fast = sum(i.price_fast_eur for i in items)
    total_max = sum(i.price_max_eur for i in items)
    return UnbundleResult(
        items=items,
        total_fast_eur=round(total_fast, 2),
        total_max_eur=round(total_max, 2),
        source="heuristic",
    )


def _gemini_unbundle(
    manifest_lines: list[str],
    title: str,
    total_retail_eur: float = 0.0,
) -> UnbundleResult | None:
    if not _gemini_key():
        return None
    try:
        import google.generativeai as genai

        genai.configure(api_key=_gemini_key())
        model = genai.GenerativeModel(os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))
        manifest_text = "\n".join(manifest_lines[:80])
        prompt = f"""Analizza questo manifest di un lotto/bancale d'asta e scomponilo in singoli articoli rivendibili.

Titolo lotto: {title}
Valore retail totale stimato: {total_retail_eur} EUR

Manifest:
{manifest_text}

Per ogni articolo rispondi SOLO con JSON valido (array):
[
  {{
    "name": "descrizione breve",
    "category": "elettronica|abbigliamento|arredo|varie",
    "platform": "ebay|vinted|subito",
    "price_fast_eur": numero (vendita rapida <=7 giorni),
    "price_max_eur": numero (profitto max <=30 giorni),
    "notes": "eventuale nota"
  }}
]

Regole routing:
- abbigliamento/brand moda -> vinted
- tecnologia/ricambi -> ebay
- ingombranti/ritiro mano -> subito
"""
        resp = model.generate_content(prompt)
        text = (resp.text or "").strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        if start < 0 or end <= start:
            return None
        raw_items = json.loads(text[start:end])
        items: list[UnbundledItem] = []
        platform_map: dict[str, Channel] = {
            "ebay": "ebay",
            "vinted": "vinted",
            "subito": "subito",
        }
        for row in raw_items:
            plat = platform_map.get(str(row.get("platform", "ebay")).lower(), "ebay")
            items.append(
                UnbundledItem(
                    name=str(row.get("name", ""))[:120],
                    category=str(row.get("category", "varie")),
                    platform=plat,
                    price_fast_eur=float(row.get("price_fast_eur", 0)),
                    price_max_eur=float(row.get("price_max_eur", 0)),
                    notes=str(row.get("notes", "")),
                )
            )
        if not items:
            return None
        return UnbundleResult(
            items=items,
            total_fast_eur=round(sum(i.price_fast_eur for i in items), 2),
            total_max_eur=round(sum(i.price_max_eur for i in items), 2),
            source="gemini",
        )
    except Exception as exc:
        print(f"[unbundler] Gemini fallito: {exc}")
        return None


def unbundle_lot(
    *,
    manifest_lines: list[str] | None = None,
    title: str = "",
    total_retail_eur: float = 0.0,
    vision_extra: dict[str, Any] | None = None,
) -> UnbundleResult:
    lines = list(manifest_lines or [])
    if vision_extra:
        lines = lines or list(vision_extra.get("manifest_lines") or [])
        if not total_retail_eur:
            total_retail_eur = float(vision_extra.get("total_retail_eur") or 0)

    if not lines:
        return UnbundleResult()

    if os.getenv("LOT_UNBUNDLER", "true").lower() in ("1", "true", "yes"):
        gemini = _gemini_unbundle(lines, title, total_retail_eur)
        if gemini and gemini.items:
            return gemini

    return _heuristic_unbundle(lines, title, total_retail_eur)


def unbundle_to_dict(result: UnbundleResult) -> dict[str, Any]:
    return {
        "source": result.source,
        "total_fast_eur": result.total_fast_eur,
        "total_max_eur": result.total_max_eur,
        "items": [
            {
                "name": i.name,
                "category": i.category,
                "platform": i.platform,
                "price_fast_eur": i.price_fast_eur,
                "price_max_eur": i.price_max_eur,
                "notes": i.notes,
            }
            for i in result.items
        ],
    }


def format_unbundle_alert(result: UnbundleResult) -> str:
    if not result.items:
        return ""
    lines = [
        f"📦 <b>Scomposizione lotto</b> ({result.source}, "
        f"{result.total_fast_eur:.0f}–{result.total_max_eur:.0f} €)",
    ]
    for item in result.items[:6]:
        lines.append(
            f"• {item.name[:50]} → <b>{item.platform}</b> "
            f"({item.price_fast_eur:.0f}/{item.price_max_eur:.0f} €)"
        )
    if len(result.items) > 6:
        lines.append(f"… +{len(result.items) - 6} articoli")
    return "\n".join(lines)


def format_unbundle_dict(data: dict[str, Any]) -> str:
    if not data.get("items"):
        return ""
    items = [
        UnbundledItem(
            name=str(i.get("name", "")),
            category=str(i.get("category", "")),
            platform=str(i.get("platform", "ebay")),  # type: ignore[arg-type]
            price_fast_eur=float(i.get("price_fast_eur", 0)),
            price_max_eur=float(i.get("price_max_eur", 0)),
            notes=str(i.get("notes", "")),
        )
        for i in data.get("items", [])
    ]
    return format_unbundle_alert(
        UnbundleResult(
            items=items,
            total_fast_eur=float(data.get("total_fast_eur", 0)),
            total_max_eur=float(data.get("total_max_eur", 0)),
            source=str(data.get("source", "")),
        )
    )
