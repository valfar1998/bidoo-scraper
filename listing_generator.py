"""Bozze annuncio pronte al copia-incolla per marketplace."""

from __future__ import annotations

import html
import re
from typing import Any, Literal

Channel = Literal["ebay", "vinted", "subito"]


def _seo_title(text: str, max_len: int = 80) -> str:
    clean = re.sub(r"\s+", " ", text.strip())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3].rsplit(" ", 1)[0] + "..."


def _platform_label(platform: Channel) -> str:
    return {
        "ebay": "eBay",
        "vinted": "Vinted",
        "subito": "Subito",
    }.get(platform, platform)


def _draft_for_item(
    *,
    name: str,
    platform: Channel,
    price_fast: float,
    price_max: float,
    category: str,
    specs: list[str],
    condition: str = "Usato — buone condizioni.",
) -> str:
    title = _seo_title(name, 80 if platform == "ebay" else 100)
    spec_block = "\n".join(f"• {s}" for s in specs[:8]) if specs else "• Vedi foto e descrizione lotto"
    desc = f"""{condition}

<b>Specifiche</b>
{spec_block}

<b>Prezzi consigliati</b>
• Vendita rapida (≤7 gg): <b>{price_fast:.0f} €</b>
• Profitto max (≤30 gg): <b>{price_max:.0f} €</b>

Spedizione tracciata / ritiro su accordo."""
    return (
        f"━━ <b>{_platform_label(platform)}</b> ━━\n"
        f"<b>Titolo</b>\n<code>{html.escape(title)}</code>\n\n"
        f"<b>Categoria</b>: {html.escape(category)}\n\n"
        f"<b>Descrizione</b>\n{desc}"
    )


def generate_listing_drafts(
    snapshot: dict[str, Any],
    unbundle: dict[str, Any] | None = None,
) -> list[str]:
    """Genera messaggi Telegram (HTML) pronti al copia-incolla."""
    title = str(snapshot.get("title") or "Articolo")
    category = str(snapshot.get("category_name") or snapshot.get("category_tag") or "varie")
    specs = list(snapshot.get("manifest_lines") or snapshot.get("packing_list") or [])[:10]
    condition = str(snapshot.get("condition") or "Usato — buone condizioni.")
    estimate = snapshot.get("estimate") or {}

    messages: list[str] = []
    items = (unbundle or {}).get("items") or []

    if items:
        header = (
            f"📝 <b>Annunci pronti</b> — {html.escape(title[:60])}\n"
            f"({len(items)} componenti dal manifest)\n"
        )
        body_parts: list[str] = []
        for row in items[:8]:
            plat: Channel = str(row.get("platform", "ebay"))  # type: ignore[assignment]
            body_parts.append(
                _draft_for_item(
                    name=str(row.get("name", title)),
                    platform=plat,
                    price_fast=float(row.get("price_fast_eur", 0)),
                    price_max=float(row.get("price_max_eur", 0)),
                    category=str(row.get("category", category)),
                    specs=specs,
                    condition=condition,
                )
            )
        messages.append(header + "\n".join(body_parts))
    else:
        fast = float(
            estimate.get("inferred_resale_eur", 0) * 0.85
            if estimate.get("inferred_resale_eur")
            else snapshot.get("retail_hint_eur", 0) * 0.5
        )
        max_p = float(estimate.get("inferred_resale_eur", fast * 1.15))
        best = str(estimate.get("best_platform") or "ebay")
        plat: Channel = best  # type: ignore[assignment]
        single = (
            f"📝 <b>Annuncio pronto</b> — {html.escape(title[:60])}\n"
            + _draft_for_item(
                name=title,
                platform=plat,
                price_fast=fast,
                price_max=max_p,
                category=category,
                specs=specs,
                condition=condition,
            )
        )
        messages.append(single)

    return _split_telegram_messages(messages)


def _split_telegram_messages(chunks: list[str], limit: int = 3900) -> list[str]:
    out: list[str] = []
    for chunk in chunks:
        if len(chunk) <= limit:
            out.append(chunk)
            continue
        parts = chunk.split("━━")
        current = ""
        for part in parts:
            segment = ("━━" + part) if part != parts[0] else part
            if len(current) + len(segment) > limit and current:
                out.append(current.strip())
                current = segment
            else:
                current += segment
        if current.strip():
            out.append(current.strip())
    return out or chunks


def build_ebay_listing_payload(
    snapshot: dict[str, Any],
    *,
    price_eur: float | None = None,
) -> dict[str, Any]:
    """Payload strutturato per eBay Inventory API."""
    title = str(snapshot.get("title") or "Articolo")
    category = str(snapshot.get("category_name") or snapshot.get("category_tag") or "varie")
    specs = list(snapshot.get("manifest_lines") or snapshot.get("packing_list") or [])[:8]
    condition = str(snapshot.get("condition") or "Usato — buone condizioni.")
    estimate = snapshot.get("estimate") or {}

    fast = float(
        estimate.get("inferred_resale_eur", 0) * 0.85
        if estimate.get("inferred_resale_eur")
        else snapshot.get("retail_hint_eur", 0) * 0.5
    )
    max_p = float(estimate.get("inferred_resale_eur", fast * 1.15))
    price = price_eur if price_eur is not None else fast

    spec_lines = "\n".join(f"• {s}" for s in specs) if specs else "• Vedi foto"
    description_plain = (
        f"{condition}\n\nSpecifiche:\n{spec_lines}\n\n"
        f"Spedizione tracciata in Italia."
    )
    aspects: dict[str, list[str]] = {}
    if category:
        aspects["Tipo"] = [category]

    return {
        "title": _seo_title(title, 80),
        "description_plain": description_plain,
        "price_eur": round(max(1.0, price), 2),
        "price_fast_eur": round(fast, 2),
        "price_max_eur": round(max_p, 2),
        "category": category,
        "condition": os.getenv("EBAY_DEFAULT_CONDITION", "USED_EXCELLENT"),
        "image_url": str(snapshot.get("image_url") or ""),
        "aspects": aspects,
    }
