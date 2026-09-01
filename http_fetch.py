"""HTTP in sola lettura: session cookie, retry, Playwright condiviso se bloccato."""

from __future__ import annotations

import json
import os
import time
from typing import Protocol

import requests

from bidoo_errors import CloudflareBlockedError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

BLOCK_MARKERS = (
    "just a moment",
    "just a moment...",
    "attention required",
    "access denied",
    "request blocked",
    "the request could not be satisfied",
    "checking your browser before accessing",
    "cf-browser-verification",
    "pardon our interruption",
    "/_sec/cp_challenge",
)

# Solo su pagine corte: "ci siamo quasi" / edgesuite compaiono anche nel sito vero.
_CHALLENGE_MARKERS = (
    "ci siamo quasi",
    "errors.edgesuite.net",
)

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['it-IT', 'it', 'en-US'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
"""


class HtmlFetcher(Protocol):
    def get_text(self, url: str) -> str: ...


def _playwright_enabled() -> bool:
    return os.getenv("USE_PLAYWRIGHT", os.getenv("BIDOO_USE_PLAYWRIGHT", "")).lower() in (
        "1",
        "true",
        "yes",
    )


def is_github_hosted() -> bool:
    """True sul runner Ubuntu di GitHub (non self-hosted, non PC di casa)."""
    env = os.getenv("RUNNER_ENVIRONMENT", "").lower()
    if env == "self-hosted":
        return False
    if env == "github-hosted":
        return True
    if os.getenv("GITHUB_ACTIONS") == "true":
        labels = os.getenv("RUNNER_LABELS", "").lower()
        if "self-hosted" in labels:
            return False
        return True
    return False


def _playwright_ok() -> bool:
    if not _playwright_enabled():
        return False
    if is_github_hosted() and os.getenv("USE_PLAYWRIGHT_ON_GITHUB", "false").lower() not in (
        "1",
        "true",
        "yes",
    ):
        return False
    return True


def _retries() -> int:
    default = "1" if is_github_hosted() else "3"
    try:
        return max(1, int(os.getenv("FETCH_RETRIES", default)))
    except ValueError:
        return 1 if is_github_hosted() else 3


def looks_like_real_page(html: str) -> bool:
    """Segnali positivi: catalogo vero, non challenge Akamai."""
    if not html:
        return False
    low = html.lower()
    if "__next_data__" in low and len(html) > 2500:
        return True
    if "catawiki" in low and len(html) > 8000:
        return True
    if "gobid.it" in low and len(html) > 5000 and "ci siamo quasi" not in low[:2500]:
        return True
    if "astagiudiziaria.com" in low and len(html) > 5000:
        return True
    if "astegiudiziarie.it" in low and len(html) > 3000:
        return True
    return False


def _looks_like_json_payload(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.startswith("<"):
        import re

        match = re.search(r"<pre[^>]*>(.*?)</pre>", raw, re.I | re.S)
        if match:
            raw = match.group(1).strip()
        else:
            raw = re.sub(r"<[^>]+>", "", raw).strip()
    if not raw or raw[0] not in "{[":
        return False
    try:
        json.loads(raw)
        return True
    except json.JSONDecodeError:
        return False


def looks_blocked(html: str) -> bool:
    if _looks_like_json_payload(html):
        return False
    if not html or len(html) < 80:
        return True
    if looks_like_real_page(html):
        return False
    text = html.lower()
    if any(marker in text for marker in BLOCK_MARKERS):
        return True
    # Challenge IT/Akamai: di solito pagina HTML piccola
    if len(html) < 8000 and any(marker in text for marker in _CHALLENGE_MARKERS):
        return True
    return False


def _headed() -> bool:
    return os.getenv("PLAYWRIGHT_HEADED", "false").lower() in ("1", "true", "yes")


def _user_data_dir() -> str | None:
    raw = os.getenv("PLAYWRIGHT_USER_DATA_DIR", "").strip()
    if raw:
        return raw
    if os.getenv("PLAYWRIGHT_PERSISTENT_PROFILE", "true").lower() in ("1", "true", "yes"):
        from pathlib import Path

        return str(Path(__file__).resolve().parent / ".playwright-profile")
    return None


def _challenge_wait_ms() -> int:
    headed = _headed()
    default = "600000" if headed else "12000"  # 10 min headed, 12s headless
    return int(os.getenv("PLAYWRIGHT_CHALLENGE_WAIT_MS", default))


def _proxy_url() -> str:
    return os.getenv("ROTATING_PROXY_URL", os.getenv("HTTP_PROXY", "")).strip()


def _flaresolverr_url() -> str:
    return os.getenv("FLARESOLVERR_URL", "").strip().rstrip("/")


def _session_proxies() -> dict[str, str] | None:
    proxy = _proxy_url()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _flaresolverr_get(url: str, *, proxy: str | None = None) -> str:
    endpoint = _flaresolverr_url()
    if not endpoint:
        return ""
    payload = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
    proxy = proxy or _proxy_url()
    if proxy:
        payload["proxy"] = {"url": proxy}
    response = requests.post(
        f"{endpoint}/v1",
        json=payload,
        timeout=75,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("status") != "ok":
        raise CloudflareBlockedError(f"FlareSolverr fallito su {url}: {body.get('message')}")
    solution = body.get("solution") or {}
    html = str(solution.get("response") or "")
    if looks_blocked(html):
        raise CloudflareBlockedError(f"FlareSolverr ancora bloccato su {url}")
    return html


class SessionFetcher:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(BROWSER_HEADERS)
        proxies = _session_proxies()
        if proxies:
            self._session.proxies.update(proxies)
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._headed_hint_shown = False
        self._challenge_prompt_shown = False
        self._persistent = False

    def __enter__(self) -> SessionFetcher:
        return self

    def __exit__(self, *args: object) -> None:
        self._close_browser()
        self._session.close()

    def warm(self, url: str) -> None:
        """Prima visita homepage: cookie/session. In headed aspetta il captcha."""
        manual = os.getenv("PLAYWRIGHT_WARM_MANUAL", "true").lower() in ("1", "true", "yes")
        if manual and _headed() and _playwright_ok():
            try:
                self._playwright_warm(url)
                return
            except Exception as exc:
                print(f"[playwright] Warm-up non riuscito: {exc}")
        try:
            self.get_text(url, allow_playwright=True)
        except Exception:
            pass

    def _playwright_warm(self, url: str) -> None:
        site = url.split("/")[2] if "://" in url else url
        wait_s = _challenge_wait_ms() // 1000
        print("[playwright] === WARM-UP (solo questa pagina) ===")
        print(f"[playwright] Sito: {site}")
        print(
            "[playwright] NON serve login/account. Basta superare "
            "'Ci siamo quasi' / captcha Akamai."
        )
        print(f"[playwright] Hai fino a ~{wait_s // 60} min. Non chiudere Chrome.")
        self._challenge_prompt_shown = False
        html = self._playwright_get(url)
        if looks_blocked(html):
            raise CloudflareBlockedError(
                f"Challenge non superato entro {wait_s}s su {url}"
            )
        self._sync_cookies_to_session()
        print("[playwright] Warm-up OK: cookie salvati, continuo lo scraping.")

    def get_text(self, url: str, *, allow_playwright: bool = True, referer: str = "") -> str:
        from proxy_health import domain_from_url, record_fetch, resolve_fetch_route

        domain = domain_from_url(url)
        last_error: Exception | None = None
        tried: set[str] = set()

        for _ in range(3):
            route = resolve_fetch_route(domain)
            if route.strategy in tried:
                break
            tried.add(route.strategy)
            start = time.time()

            if route.strategy == "flaresolverr":
                try:
                    html = _flaresolverr_get(url, proxy=route.proxy_url)
                    if html:
                        record_fetch(
                            domain,
                            latency_ms=(time.time() - start) * 1000,
                            status_code=200,
                        )
                        return html
                except Exception as exc:
                    record_fetch(
                        domain,
                        latency_ms=(time.time() - start) * 1000,
                        blocked=True,
                        error=str(exc),
                    )
                    last_error = exc
                    continue

            old_proxies = dict(self._session.proxies)
            try:
                if route.proxy_url:
                    self._session.proxies.update(
                        {"http": route.proxy_url, "https": route.proxy_url}
                    )
                elif route.strategy == "direct":
                    self._session.proxies.clear()

                for attempt in range(_retries()):
                    try:
                        headers = dict(BROWSER_HEADERS)
                        if referer:
                            headers["Referer"] = referer
                            headers["Sec-Fetch-Site"] = "same-origin"
                        req_start = time.time()
                        response = self._session.get(url, headers=headers, timeout=45)
                        latency = (time.time() - req_start) * 1000
                        if response.status_code in (403, 429, 503):
                            record_fetch(
                                domain,
                                latency_ms=latency,
                                status_code=response.status_code,
                                blocked=True,
                            )
                            last_error = CloudflareBlockedError(
                                f"HTTP {response.status_code} su {url}"
                            )
                            time.sleep(1.2 * (attempt + 1))
                            continue
                        response.raise_for_status()
                        html = response.text or ""
                        if looks_blocked(html):
                            record_fetch(
                                domain,
                                latency_ms=latency,
                                status_code=response.status_code,
                                blocked=True,
                            )
                            last_error = CloudflareBlockedError(f"Pagina challenge su {url}")
                            time.sleep(0.8 * (attempt + 1))
                            continue
                        record_fetch(
                            domain,
                            latency_ms=latency,
                            status_code=response.status_code,
                        )
                        return html
                    except CloudflareBlockedError as exc:
                        last_error = exc
                    except Exception as exc:
                        last_error = exc
                        time.sleep(0.8 * (attempt + 1))
            finally:
                self._session.proxies.clear()
                self._session.proxies.update(old_proxies)

        if _flaresolverr_url() and "flaresolverr" not in tried:
            try:
                start = time.time()
                html = _flaresolverr_get(url)
                if html:
                    record_fetch(
                        domain,
                        latency_ms=(time.time() - start) * 1000,
                        status_code=200,
                    )
                    return html
            except Exception as exc:
                record_fetch(
                    domain,
                    latency_ms=(time.time() - start) * 1000,
                    blocked=True,
                    error=str(exc),
                )
                last_error = exc
        if allow_playwright and _playwright_ok():
            try:
                html = self._playwright_get(url)
                if html and not looks_blocked(html):
                    return html
                if html:
                    last_error = CloudflareBlockedError(f"Playwright ancora bloccato su {url}")
            except Exception as exc:
                last_error = exc
        raise last_error or CloudflareBlockedError(f"Fetch fallito: {url}")

    def get_json(
        self,
        url: str,
        params: dict | None = None,
        extra_headers: dict | None = None,
    ) -> dict | list:
        last_error: Exception | None = None
        # Senza brotli, Accept-Encoding: br corrompe il body (Remundo/Shopify JSON).
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": BROWSER_HEADERS["Accept-Language"],
            "Accept-Encoding": "gzip, deflate",
            **(extra_headers or {}),
        }
        for attempt in range(_retries()):
            try:
                response = self._session.get(url, headers=headers, params=params, timeout=45)
                if response.status_code in (403, 429, 503):
                    last_error = CloudflareBlockedError(f"HTTP {response.status_code} JSON {url}")
                    time.sleep(1.0 * (attempt + 1))
                    continue
                response.raise_for_status()
                data = response.json()
                if data is None:
                    last_error = ValueError("JSON vuoto")
                    continue
                return data
            except Exception as exc:
                last_error = exc
                time.sleep(0.8 * (attempt + 1))
        # API JSON: no Playwright di default (carica in secondi o fallisce; evita attese 10 min).
        use_pw = os.getenv("JSON_USE_PLAYWRIGHT", "false").lower() in ("1", "true", "yes")
        if not use_pw:
            raise last_error or CloudflareBlockedError(f"JSON fallito: {url}")
        if _playwright_ok():
            full = url
            if params:
                from urllib.parse import urlencode

                full = url + ("&" if "?" in url else "?") + urlencode(params)
            try:
                raw = self._playwright_get(full)
                if raw and not looks_blocked(raw):
                    text = raw.strip()
                    start = text.find("{")
                    start_list = text.find("[")
                    if start_list != -1 and (start == -1 or start_list < start):
                        start = start_list
                    if start != -1:
                        chunk = text[start:]
                        end_obj = chunk.rfind("}")
                        end_arr = chunk.rfind("]")
                        end = max(end_obj, end_arr)
                        if end > 0:
                            return json.loads(chunk[: end + 1])
            except Exception as exc:
                last_error = exc
        raise last_error or CloudflareBlockedError(f"JSON fallito: {url}")

    def _ensure_browser(self) -> None:
        if self._context is not None:
            if self._persistent:
                return
            if self._browser is not None:
                try:
                    if self._browser.is_connected():
                        return
                except Exception:
                    pass
            self._close_browser()
        from pathlib import Path
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        headed = _headed()
        if headed and not self._headed_hint_shown:
            self._headed_hint_shown = True
            profile = _user_data_dir()
            print(
                "[playwright] Chrome headed: lascia aperta la finestra fino alla fine "
                "del run. Se chiudi: errore 'browser has been closed'."
            )
            if profile:
                print(
                    f"[playwright] Profilo persistente: {profile} "
                    "(cookie Akamai riusati al giro dopo)."
                )
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
        profile = _user_data_dir()
        if profile:
            Path(profile).mkdir(parents=True, exist_ok=True)
            try:
                self._context = self._pw.chromium.launch_persistent_context(
                    profile,
                    channel="chrome",
                    headless=not headed,
                    args=args,
                    locale="it-IT",
                    timezone_id="Europe/Rome",
                    viewport={"width": 1366, "height": 900},
                    user_agent=USER_AGENT,
                )
                self._context.add_init_script(_STEALTH_JS)
                self._browser = None
                self._persistent = True
                self._page = None
                return
            except Exception as exc:
                msg = str(exc).lower()
                if headed and (
                    "has been closed" in msg
                    or "browser has been closed" in msg
                    or "sessione del browser esistente" in msg
                    or "existing browser session" in msg
                    or "user data directory" in msg
                    or "already in use" in msg
                ):
                    raise CloudflareBlockedError(
                        "Profilo Chrome già in uso. Chiudi TUTTE le finestre Chrome "
                        "(anche quella del login manuale), termina altri "
                        "'python monitor_*.py' in esecuzione, poi rilancia."
                    ) from exc
                print(f"[playwright] Profilo persistente fallito ({exc}), browser temporaneo.")
                self._persistent = False

        launch = {"headless": not headed, "args": args}
        proxy = _proxy_url()
        if proxy:
            launch["proxy"] = {"server": proxy}
        try:
            self._browser = self._pw.chromium.launch(channel="chrome", **launch)
        except Exception:
            self._browser = self._pw.chromium.launch(**launch)
        self._context = self._browser.new_context(
            user_agent=USER_AGENT,
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={
                "Accept-Language": "it-IT,it;q=0.9",
            },
        )
        self._context.add_init_script(_STEALTH_JS)
        self._page = None

    def _sync_cookies_to_session(self) -> None:
        if self._context is None:
            return
        try:
            for cookie in self._context.cookies():
                domain = cookie.get("domain") or ""
                if domain.startswith("."):
                    domain = domain[1:]
                self._session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=domain or None,
                    path=cookie.get("path") or "/",
                )
        except Exception:
            pass

    def _wait_page_html(self, page) -> str:
        wait_ms = int(os.getenv("PLAYWRIGHT_WAIT_MS", "4500"))
        page.wait_for_timeout(wait_ms)
        html = page.content()
        if _looks_like_json_payload(html):
            return html
        headed = _headed()
        challenge_ms = _challenge_wait_ms()
        poll_ms = 2000
        extra_loops = max(8, challenge_ms // poll_ms) if headed else 8
        if headed and looks_blocked(html) and not self._challenge_prompt_shown:
            self._challenge_prompt_shown = True
            print(
                "[playwright] Challenge Akamai: completa il check nella finestra Chrome "
                f"(~{challenge_ms // 1000}s max). Login NON richiesto."
            )
        for i in range(extra_loops):
            if not looks_blocked(html) and len(html) > 400:
                if headed and i > 0:
                    print("[playwright] Check superato, continuo…")
                break
            if headed and looks_blocked(html) and i > 0 and i % 15 == 0:
                left = (extra_loops - i) * poll_ms // 1000
                title = ""
                try:
                    title = (page.title() or "").strip()[:60]
                except Exception:
                    pass
                print(
                    f"[playwright] In attesa… ~{left}s rimasti. "
                    f"Titolo pagina: '{title or '?'}' len={len(html)}"
                )
            page.wait_for_timeout(poll_ms)
            html = page.content()
        return html

    def _page_alive(self) -> bool:
        if self._page is None:
            return False
        try:
            _ = self._page.url
            return True
        except Exception:
            return False

    def _playwright_get(self, url: str) -> str:
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                self._ensure_browser()
                assert self._context is not None
                if not self._page_alive():
                    if self._persistent:
                        pages = self._context.pages
                        self._page = pages[0] if pages else self._context.new_page()
                    else:
                        self._page = self._context.new_page()
                page = self._page
                assert page is not None
                goto_ms = int(os.getenv("PLAYWRIGHT_GOTO_MS", "35000"))
                response = page.goto(url, wait_until="domcontentloaded", timeout=goto_ms)
                status = response.status if response is not None else 0
                html = self._wait_page_html(page)
                title = ""
                try:
                    title = (page.title() or "").lower()
                except Exception:
                    pass
                body_l = (html or "")[:4000].lower()
                if status == 404 or "404" in title or "page not found" in body_l:
                    raise CloudflareBlockedError(f"HTTP 404 (pagina non trovata) su {url}")
                if not looks_blocked(html):
                    self._sync_cookies_to_session()
                return html
            except Exception as exc:
                last_err = exc
                msg = str(exc).lower()
                if "has been closed" in msg or "target closed" in msg or "browser" in msg:
                    print(
                        "[playwright] Browser chiuso (finestra X o crash). "
                        "Riapro… Non chiudere Chrome mentre gira lo script."
                    )
                    self._close_browser()
                    continue
                raise
        raise last_err or CloudflareBlockedError(f"Playwright fallito: {url}")

    def _close_browser(self) -> None:
        if self._page is not None and not self._persistent:
            try:
                self._page.close()
            except Exception:
                pass
        self._page = None
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        self._context = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
        self._browser = None
        self._persistent = False
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None


def fetch_with_playwright(url: str) -> str:
    """Compat: una pagina isolata. Preferisci SessionFetcher.get_text (browser riusato)."""
    with SessionFetcher() as fetcher:
        return fetcher._playwright_get(url)


def open_fetcher() -> SessionFetcher:
    return SessionFetcher()
