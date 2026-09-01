"""Ricerca live prezzi di mercato (Vinted + eBay venduti) per stima rivendita."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote_plus

from brands import find_brand
from comps import CompRow, match_brand_comp, match_comp, stdev_of
from database import connect, db_enabled, ensure_db
from flip_rules import infer_flip_tag
from money import parse_euro

if TYPE_CHECKING:
    from http_fetch import SessionFetcher

CACHE_FILE = Path(__file__).resolve().parent / ".market_cache.json"
VINTED_API = "https://www.vinted.it/api/v2/catalog/items"

_STOPWORDS = frozenset(
    {
        "il",
        "lo",
        "la",
        "i",
        "gli",
        "le",
        "un",
        "una",
        "uno",
        "di",
        "da",
        "del",
        "della",
        "dei",
        "delle",
        "per",
        "con",
        "tra",
        "fra",
        "sul",
        "sulla",
        "nel",
        "nella",
        "the",
        "and",
        "for",
        "new",
        "nuovo",
        "nuova",
        "usato",
        "usata",
        "originale",
        "offerta",
        "asta",
        "lotto",
        "stock",
        "pezzi",
        "pezzo",
        "articolo",
        "oggetto",
        "versione",
        "edition",
        "italiano",
        "italiana",
        "sigillato",
        "scatola",
        "con",
        "senza",
        "come",
        "foto",
        "vedi",
        "nintendo",
        "switch",
        "playstation",
        "xbox",
        "sony",
        "microsoft",
    }
)

_BUNDLE_HINTS = (
    "lotto",
    "bundle",
    "pack",
    "console",
    "accessori",
    "accessory",
    " joy-con",
    "joycon",
    " + ",
    "+ ",
    " con ",
    " inclus",
    " completo",
    " kit ",
    " set ",
    " giochi",
    " games",
    "x ",
)

_JUNK_TITLE = re.compile(
    r"\b(ricambio|spare|rotto|guasto|difettos|non funzion|per parti|solo scatola)\b",
    re.I,
)


_VINTED_SESSION_OK = False


def ensure_vinted_session(fetcher: SessionFetcher) -> None:
    """Cookie Vinted per API catalog (401 senza sessione)."""
    global _VINTED_SESSION_OK
    if _VINTED_SESSION_OK:
        return
    try:
        fetcher.get_text("https://www.vinted.it/", allow_playwright=False)
    except Exception:
        pass
    try:
        fetcher.get_json(
            VINTED_API,
            params={"search_text": "nike", "per_page": "1", "page": "1"},
            extra_headers={
                "Accept": "application/json",
                "Referer": "https://www.vinted.it/",
            },
        )
        _VINTED_SESSION_OK = True
        return
    except Exception:
        pass
    try:
        fetcher.warm("https://www.vinted.it/")
        _VINTED_SESSION_OK = True
    except Exception as exc:
        print(f"[market] Sessione Vinted non pronta: {exc}")


def market_lookup_enabled(profile_key: str = "") -> bool:
    flag = os.getenv("MARKET_LOOKUP", "true").lower() in ("1", "true", "yes")
    if not flag:
        return False
    only = os.getenv("MARKET_LOOKUP_SOURCES", "ebay_source,vinted_source").strip().lower()
    if only in ("", "*", "all"):
        return True
    allowed = {item.strip() for item in only.split(",") if item.strip()}
    return profile_key in allowed


def lookup_channels(profile_key: str) -> tuple[bool, bool]:
    """Canali ricerca prezzi live: (Vinted annunci, eBay venduti). Solo questi due."""
    if profile_key == "ebay_source":
        return True, False
    if profile_key == "vinted_source":
        return False, True
    return True, True


def build_market_query(title: str, *, category_tag: str = "") -> str:
    text = re.sub(r"\([^)]{0,80}\)", " ", title)
    text = re.sub(r"\[[^\]]{0,80}\]", " ", text)
    text = re.sub(r"\b(eur|€)\s*[\d.,]+\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip().lower()
    tokens: list[str] = []
    for raw in re.split(r"\W+", text):
        word = raw.strip()
        if len(word) < 2 and not word.isdigit():
            continue
        if word in _STOPWORDS:
            continue
        if word.isdigit() and len(word) <= 2:
            tokens.append(word)
            continue
        if len(word) >= 3 or word.isdigit():
            tokens.append(word)
    # Tieni numeri di modello (19, 360, ps5) e parole distintive.
    deduped: list[str] = []
    for word in tokens:
        if word not in deduped:
            deduped.append(word)
    if not deduped:
        return text[:60]
    query = " ".join(deduped[:8])
    if category_tag == "videogiochi" and len(deduped) >= 2:
        # Evita query solo-console quando c'è un titolo gioco.
        non_platform = [w for w in deduped if w not in {"switch", "ps4", "ps5", "xbox"}]
        if non_platform:
            query = " ".join(non_platform[:6])
    return query[:80]


def is_generic_comp(comp: CompRow, title: str) -> bool:
    product = (comp.product or "").lower()
    if not product or product.startswith("brand:"):
        return True
    text = title.lower()
    if product not in text:
        return False
    title_tokens = {
        t
        for t in re.split(r"\W+", text)
        if (len(t) >= 3 or t.isdigit()) and t not in _STOPWORDS
    }
    product_tokens = set(re.split(r"\W+", product))
    extra = [t for t in title_tokens if t not in product_tokens]
    return len(extra) >= 2


@dataclass(frozen=True)
class MarketLookupResult:
    query: str
    avg_price_vinted: float
    avg_price_ebay: float
    n_vinted: int
    n_ebay: int
    stdev: float
    sample_titles: tuple[str, ...]
    reliable: bool

    def to_comp_row(self) -> CompRow:
        prices = []
        if self.avg_price_vinted > 0:
            prices.append(self.avg_price_vinted)
        if self.avg_price_ebay > 0:
            prices.append(self.avg_price_ebay)
        return CompRow(
            product=f"live:{self.query}",
            avg_price_ebay=self.avg_price_ebay,
            avg_price_vinted=self.avg_price_vinted,
            stdev=self.stdev,
            n_ebay=self.n_ebay,
            n_vinted=self.n_vinted,
            updated_at=time.time(),
        )


def _cache_ttl_s() -> int:
    try:
        hours = float(os.getenv("MARKET_CACHE_HOURS", "24"))
    except ValueError:
        hours = 24.0
    return max(3600, int(hours * 3600))


def _load_cache() -> dict:
    ensure_db()
    if db_enabled():
        data: dict = {}
        now = time.time()
        with connect() as conn:
            rows = conn.execute(
                "SELECT cache_key, payload_json, expires_at FROM market_cache WHERE expires_at > ?",
                (now,),
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                continue
            data[str(row["cache_key"])] = payload
        return data
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(data: dict) -> None:
    ensure_db()
    if db_enabled():
        ttl = _cache_ttl_s()
        now = time.time()
        with connect() as conn:
            conn.execute("DELETE FROM market_cache")
            for key, payload in data.items():
                ts = float(payload.get("ts") or now)
                conn.execute(
                    """
                    INSERT INTO market_cache(cache_key, payload_json, expires_at)
                    VALUES(?, ?, ?)
                    """,
                    (str(key), json.dumps(payload), ts + ttl),
                )
        return
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _cache_get(query: str) -> MarketLookupResult | None:
    entry = _load_cache().get(query.lower())
    if not entry:
        return None
    if time.time() - float(entry.get("ts") or 0) > _cache_ttl_s():
        return None
    return MarketLookupResult(
        query=str(entry.get("query") or query),
        avg_price_vinted=float(entry.get("avg_price_vinted") or 0),
        avg_price_ebay=float(entry.get("avg_price_ebay") or 0),
        n_vinted=int(entry.get("n_vinted") or 0),
        n_ebay=int(entry.get("n_ebay") or 0),
        stdev=float(entry.get("stdev") or 0),
        sample_titles=tuple(entry.get("sample_titles") or ()),
        reliable=bool(entry.get("reliable")),
    )


def _cache_put(result: MarketLookupResult) -> None:
    data = _load_cache()
    data[result.query.lower()] = {
        "query": result.query,
        "avg_price_vinted": result.avg_price_vinted,
        "avg_price_ebay": result.avg_price_ebay,
        "n_vinted": result.n_vinted,
        "n_ebay": result.n_ebay,
        "stdev": result.stdev,
        "sample_titles": list(result.sample_titles[:5]),
        "reliable": result.reliable,
        "ts": time.time(),
    }
    # Evita file enorme.
    if len(data) > 500:
        ranked = sorted(data.items(), key=lambda item: float(item[1].get("ts") or 0), reverse=True)
        data = dict(ranked[:400])
    _save_cache(data)


def _title_match_score(query: str, result_title: str) -> float:
    q_tokens = {
        t for t in re.split(r"\W+", query.lower()) if len(t) >= 2 and t not in _STOPWORDS
    }
    if not q_tokens:
        return 0.0
    title = result_title.lower()
    hits = sum(1 for token in q_tokens if token in title)
    return hits / len(q_tokens)


def _looks_like_bundle(title: str, query: str) -> bool:
    low = title.lower()
    if any(hint in low for hint in _BUNDLE_HINTS):
        # Bundle ok se la query chiede esplicitamente un lotto.
        if "lotto" not in query.lower() and "stock" not in query.lower():
            return True
    return False


def _vinted_prices(fetcher: SessionFetcher, query: str) -> list[tuple[float, str]]:
    try:
        data = fetcher.get_json(
            VINTED_API,
            params={
                "search_text": query,
                "per_page": "40",
                "page": "1",
                "order": "relevance",
            },
            extra_headers={
                "Accept": "application/json",
                "Referer": f"https://www.vinted.it/catalog?search_text={quote_plus(query)}",
            },
        )
    except Exception as exc:
        print(f"[market] Vinted API '{query}': {exc}")
        return []
    if not isinstance(data, dict):
        return []
    rows: list[tuple[float, str]] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title or _JUNK_TITLE.search(title):
            continue
        if _looks_like_bundle(title, query):
            continue
        score = _title_match_score(query, title)
        if score < 0.55:
            continue
        price = _vinted_item_price(item)
        if price <= 0:
            continue
        rows.append((price, title))
    return rows


def _vinted_item_price(item: dict) -> float:
    for key in ("price", "total_item_price", "discount_price"):
        raw = item.get(key)
        if isinstance(raw, dict):
            amount = raw.get("amount") or raw.get("numeric")
            try:
                return float(str(amount).replace(",", "."))
            except (TypeError, ValueError):
                continue
        if raw not in (None, ""):
            parsed = parse_euro(str(raw))
            if parsed:
                return parsed
            try:
                return float(str(raw).replace(",", "."))
            except ValueError:
                continue
    return 0.0


def _ebay_sold_prices(fetcher: SessionFetcher, query: str) -> list[float]:
    url = (
        "https://www.ebay.it/sch/i.html?_nkw="
        + quote_plus(query)
        + "&LH_Complete=1&LH_Sold=1&rt=nc&_ipg=60"
    )
    try:
        html = fetcher.get_text(url, allow_playwright=False)
    except Exception as exc:
        print(f"[market] eBay venduti '{query}': {exc}")
        return []
    prices: list[float] = []
    for match in re.finditer(r"s-item__price[^>]*>([^<]{2,40})<", html, re.I):
        parsed = parse_euro(match.group(1))
        if parsed and 3 <= parsed <= 800:
            prices.append(parsed)
    if len(prices) < 3:
        for match in re.finditer(r"(?:EUR|€)\s*([\d.,]+)", html):
            parsed = parse_euro(match.group(0))
            if parsed and 3 <= parsed <= 800:
                prices.append(parsed)
    prices.sort()
    if len(prices) >= 8:
        cut = max(1, len(prices) // 10)
        prices = prices[cut:-cut]
    return prices[:30]


def _trim_outliers(values: list[float]) -> list[float]:
    if len(values) < 4:
        return values
    sorted_vals = sorted(values)
    mid = len(sorted_vals) // 2
    median = sorted_vals[mid]
    kept = [v for v in sorted_vals if median * 0.35 <= v <= median * 2.8]
    return kept or sorted_vals


def lookup_market(
    title: str,
    fetcher: SessionFetcher,
    *,
    category_tag: str = "",
    profile_key: str = "",
    use_vinted: bool | None = None,
    use_ebay: bool | None = None,
) -> MarketLookupResult | None:
    if use_vinted is None or use_ebay is None:
        default_v, default_e = lookup_channels(profile_key)
        if use_vinted is None:
            use_vinted = default_v
        if use_ebay is None:
            use_ebay = default_e
    query = build_market_query(title, category_tag=category_tag or infer_flip_tag(title))
    if len(query.strip()) < 4:
        return None
    cached = _cache_get(query)
    if cached:
        return cached

    delay = float(os.getenv("MARKET_LOOKUP_DELAY_S", "0.35"))
    vinted_rows: list[tuple[float, str]] = []
    if use_vinted:
        vinted_rows = _vinted_prices(fetcher, query)
        if delay > 0:
            time.sleep(delay)
    ebay_vals: list[float] = []
    if use_ebay:
        ebay_vals = _ebay_sold_prices(fetcher, query)

    vinted_prices = _trim_outliers([price for price, _ in vinted_rows])
    ebay_prices = _trim_outliers(ebay_vals)
    min_vinted = int(os.getenv("MARKET_MIN_VINTED_SAMPLES", "2"))
    min_ebay = int(os.getenv("MARKET_MIN_EBAY_SAMPLES", "2"))
    min_total = int(os.getenv("MARKET_MIN_SAMPLES", "2"))

    enough = False
    if use_vinted and len(vinted_prices) >= min_vinted:
        enough = True
    if use_ebay and len(ebay_prices) >= min_ebay:
        enough = True
    if not use_vinted and not use_ebay:
        enough = len(vinted_prices) + len(ebay_prices) >= min_total
    if not enough:
        return None

    vinted_avg = sum(vinted_prices) / len(vinted_prices) if vinted_prices else 0.0
    ebay_avg = sum(ebay_prices) / len(ebay_prices) if ebay_prices else 0.0
    all_prices = vinted_prices + ebay_prices
    stdev = stdev_of(all_prices) if len(all_prices) >= 2 else 0.0
    reliable = (
        (use_vinted and len(vinted_prices) >= max(3, min_vinted))
        or (use_ebay and len(ebay_prices) >= max(3, min_ebay))
        or (
            use_vinted
            and use_ebay
            and len(vinted_prices) >= min_vinted
            and len(ebay_prices) >= min_ebay
        )
    )
    samples = tuple(title for _, title in vinted_rows[:5])
    result = MarketLookupResult(
        query=query,
        avg_price_vinted=vinted_avg,
        avg_price_ebay=ebay_avg,
        n_vinted=len(vinted_prices),
        n_ebay=len(ebay_prices),
        stdev=stdev,
        sample_titles=samples,
        reliable=reliable,
    )
    _cache_put(result)
    return result


def resolve_comp(
    listing,
    profile,
    comps: list[CompRow] | None,
    fetcher: SessionFetcher | None,
) -> tuple[CompRow | None, str]:
    """Comp per stima: preferisce ricerca live, evita match generici tipo 'nintendo' su un gioco."""
    category_tag = infer_flip_tag(listing.title)
    try:
        from comp_embeddings import match_comp_semantic

        semantic, score = match_comp_semantic(listing.title, comps)
        if semantic and score >= float(os.getenv("SEMANTIC_COMPS_THRESHOLD", "0.72")):
            return semantic, f"sem:{semantic.product} ({score:.2f})"
    except Exception:
        pass
    static = match_comp(listing.title, comps) or match_brand_comp(find_brand(listing.title), comps)

    if market_lookup_enabled(profile.key) and fetcher is not None:
        use_vinted, use_ebay = lookup_channels(profile.key)
        live = lookup_market(
            listing.title,
            fetcher,
            category_tag=category_tag,
            profile_key=profile.key,
            use_vinted=use_vinted,
            use_ebay=use_ebay,
        )
        min_vinted = int(os.getenv("MARKET_MIN_VINTED_SAMPLES", "2"))
        min_ebay = int(os.getenv("MARKET_MIN_EBAY_SAMPLES", "2"))
        ok = live and (
            live.reliable
            or (use_vinted and live.n_vinted >= min_vinted)
            or (use_ebay and live.n_ebay >= min_ebay)
        )
        if ok and live:
            if profile.key == "vinted_source":
                note = (
                    f"eBay venduti live '{live.query}' ({live.n_ebay} vendite"
                    f", ~{live.avg_price_ebay:.0f} €)"
                )
            else:
                note = f"Vinted live '{live.query}' ({live.n_vinted} annunci"
                if live.n_ebay:
                    note += f", eBay venduti {live.n_ebay}"
                note += f", ~{live.avg_price_vinted:.0f} €)"
            return live.to_comp_row(), note

    if static and not is_generic_comp(static, listing.title):
        return static, static.product

    if static and is_generic_comp(static, listing.title):
        return None, f"comp generico '{static.product}' (serve ricerca live)"

    return None, "nessun comp"
