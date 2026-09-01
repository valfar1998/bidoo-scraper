"""Analisi foto lotti con Gemini Flash (EAN, modello, packaging, OCR manifest)."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

from database import get_vision_cache, save_vision_cache
from listing import SourceListing

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

_MANIFEST_HINTS = re.compile(
    r"\b(manifest|distinta|packing\s*list|bolla|elenco|foglio|lista\s+prodott)\b",
    re.I,
)


def vision_enabled() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip()) and os.getenv(
        "VISION_ANALYSIS", "true"
    ).lower() in ("1", "true", "yes")


def _model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()


def _image_url(listing: SourceListing) -> str:
    extra = listing.extra or {}
    return str(extra.get("image_url") or "").strip()


def _manifest_mode(listing: SourceListing) -> bool:
    extra = listing.extra or {}
    if extra.get("packing_list"):
        return True
    if listing.source in ("remundo",) or "bancale" in listing.title.lower():
        return True
    return bool(_MANIFEST_HINTS.search(listing.title))


def _build_prompt(title: str, *, manifest: bool) -> str:
    if manifest:
        return (
            "Questa foto mostra un manifest, distinta o bolla d'accompagnamento di un bancale/lotto. "
            "Fai OCR riga per riga. Rispondi SOLO con JSON valido senza markdown:\n"
            '{"is_manifest":true,"manifest_lines":[{"description":"","qty":0,"retail_eur":0}],'
            '"total_retail_eur":0,"brand":"","model":"","ean":"","items":[],"packaging_intact":true,'
            '"confidence":0,"notes":""}\n'
            f"Titolo asta: {title[:200]}"
        )
    return (
        "Analizza questa foto di un lotto d'asta o bancale. "
        "Rispondi SOLO con JSON valido senza markdown:\n"
        '{"is_manifest":false,"manifest_lines":[],"total_retail_eur":0,'
        '"brand":"","model":"","ean":"","items":[],"packaging_intact":true,'
        '"confidence":0,"notes":""}\n'
        f"Titolo asta: {title[:200]}"
    )


def analyze_listing_image(listing: SourceListing, *, force: bool = False) -> dict[str, Any] | None:
    url = _image_url(listing)
    if not url or not vision_enabled():
        return None
    listing_id = listing.history_key
    if not force:
        cached = get_vision_cache(listing_id)
        if cached and cached.get("_image_url") == url:
            return {key: value for key, value in cached.items() if not key.startswith("_")}
    analysis = _call_gemini(url, listing.title, manifest=_manifest_mode(listing))
    if analysis:
        save_vision_cache(listing_id, url, analysis)
    return analysis


def _call_gemini(image_url: str, title: str, *, manifest: bool) -> dict[str, Any] | None:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    prompt = _build_prompt(title, manifest=manifest)
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"file_data": {"mime_type": "image/jpeg", "file_uri": image_url}},
                ]
            }
        ],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024 if manifest else 512},
    }
    try:
        response = requests.post(
            GEMINI_URL.format(model=_model()),
            params={"key": api_key},
            json=payload,
            timeout=60 if manifest else 45,
        )
        if response.status_code != 200:
            return _call_gemini_inline(image_url, title, api_key, manifest=manifest)
        text = _extract_text(response.json())
        return _parse_json(text)
    except Exception:
        return _call_gemini_inline(image_url, title, api_key, manifest=manifest)


def _call_gemini_inline(
    image_url: str, title: str, api_key: str, *, manifest: bool
) -> dict[str, Any] | None:
    try:
        image = requests.get(image_url, timeout=20)
        image.raise_for_status()
    except Exception:
        return None
    mime = image.headers.get("Content-Type", "image/jpeg").split(";")[0]
    import base64

    prompt = _build_prompt(title, manifest=manifest)
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(image.content).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024 if manifest else 512},
    }
    try:
        response = requests.post(
            GEMINI_URL.format(model=_model()),
            params={"key": api_key},
            json=payload,
            timeout=90 if manifest else 60,
        )
        response.raise_for_status()
        return _parse_json(_extract_text(response.json()))
    except Exception:
        return None


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts") or []
    return "".join(str(part.get("text") or "") for part in parts)


def _parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    manifest_lines = []
    for row in data.get("manifest_lines") or []:
        if not isinstance(row, dict):
            continue
        manifest_lines.append(
            {
                "description": str(row.get("description") or "").strip()[:120],
                "qty": int(row.get("qty") or 0),
                "retail_eur": float(row.get("retail_eur") or 0),
            }
        )
    total_retail = float(data.get("total_retail_eur") or 0)
    if total_retail <= 0 and manifest_lines:
        total_retail = sum(
            line["retail_eur"] * max(1, line["qty"]) for line in manifest_lines
        )
    return {
        "is_manifest": bool(data.get("is_manifest")),
        "manifest_lines": manifest_lines[:40],
        "total_retail_eur": round(total_retail, 2),
        "brand": str(data.get("brand") or "").strip(),
        "model": str(data.get("model") or "").strip(),
        "ean": str(data.get("ean") or "").strip(),
        "items": [str(item) for item in (data.get("items") or [])[:12]],
        "packaging_intact": bool(data.get("packaging_intact", True)),
        "confidence": int(data.get("confidence") or 0),
        "notes": str(data.get("notes") or "").strip()[:240],
    }


def apply_vision_to_listing(listing: SourceListing, analysis: dict[str, Any]) -> SourceListing:
    extra = dict(listing.extra or {})
    extra["vision"] = analysis
    if analysis.get("ean"):
        extra["ean"] = analysis["ean"]
    if analysis.get("model"):
        extra["detected_model"] = analysis["model"]
    if analysis.get("brand"):
        extra["detected_brand"] = analysis["brand"]
    if analysis.get("is_manifest") and analysis.get("manifest_lines"):
        extra["packing_list"] = True
        extra["manifest_lines"] = analysis["manifest_lines"]
    total_retail = float(analysis.get("total_retail_eur") or 0)
    retail_hint = listing.retail_hint_eur
    if total_retail >= 20:
        retail_hint = total_retail
    return SourceListing(
        source=listing.source,
        listing_id=listing.listing_id,
        title=listing.title,
        url=listing.url,
        current_price_eur=listing.current_price_eur,
        shipping_eur=listing.shipping_eur,
        retail_hint_eur=retail_hint,
        buy_now_eur=listing.buy_now_eur,
        bids=listing.bids,
        remaining_text=listing.remaining_text,
        remaining_seconds=listing.remaining_seconds,
        location=listing.location,
        category_tag=listing.category_tag,
        extra=extra,
    )
