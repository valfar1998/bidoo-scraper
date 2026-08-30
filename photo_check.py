"""Controllo foto: HEAD + dimensioni header JPEG/PNG. Su GitHub cloud si salta il fetch."""

from __future__ import annotations

import os
from typing import Literal
import requests

from http_fetch import BROWSER_HEADERS, is_github_hosted
from listing import SourceListing

PhotoVerdict = Literal["ok", "missing", "tiny", "stock", "unknown"]

STOCK_HINTS = (
    "placeholder",
    "no-image",
    "noimage",
    "nophoto",
    "default.jpg",
    "default.png",
    "stock",
    "unsplash",
    "shutterstock",
    "gettyimages",
    "istock",
    "empty.",
    "missing.",
)


def inspect_image(listing: SourceListing) -> PhotoVerdict:
    extra = listing.extra or {}
    if "has_image" not in extra and "image_url" not in extra:
        return "unknown"
    url = str(extra.get("image_url") or "").strip()
    if extra.get("has_image") is False or not url:
        return "missing"
    lowered = url.lower()
    if any(hint in lowered for hint in STOCK_HINTS):
        return "stock"
    if is_github_hosted() or os.getenv("SKIP_IMAGE_CHECK", "").lower() in ("1", "true"):
        return "ok"
    try:
        response = requests.head(url, headers=BROWSER_HEADERS, timeout=4, allow_redirects=True)
        length = int(response.headers.get("Content-Length") or 0)
        if 0 < length < 10_000:
            return "tiny"
        ctype = (response.headers.get("Content-Type") or "").lower()
        if "image" not in ctype and response.status_code >= 400:
            return "missing"
    except Exception:
        return "unknown"
    width, height = _probe_pixels(url)
    if width and height and min(width, height) < 300:
        return "tiny"
    return "ok"


def _probe_pixels(url: str) -> tuple[int, int]:
    try:
        response = requests.get(
            url,
            headers={**BROWSER_HEADERS, "Range": "bytes=0-32767"},
            timeout=5,
            stream=True,
        )
        data = response.content[:32768]
    except Exception:
        return 0, 0
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
        return w, h
    sof = data.find(b"\xff\xc0")
    if sof == -1:
        sof = data.find(b"\xff\xc2")
    if sof != -1 and sof + 9 <= len(data):
        h = int.from_bytes(data[sof + 5 : sof + 7], "big")
        w = int.from_bytes(data[sof + 7 : sof + 9], "big")
        return w, h
    return 0, 0
