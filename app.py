"""
Multi-Engine File Search Tool
Searches multiple search engines simultaneously for direct file download links
using filetype dork queries.
"""

import concurrent.futures
import json
import queue
import random
import re
import threading
import time
from collections import deque
from urllib.parse import urlencode, urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, jsonify, render_template, request, send_from_directory, stream_with_context

app = Flask(__name__)


class RateLimitedError(Exception):
    """Raised when a search engine returns a rate-limit / CAPTCHA response."""


class ProxyManager:
    """Thread-safe rotating proxy pool. Proxies are burned when they get blocked."""

    def __init__(self, proxy_list: list[str]):
        self._lock = threading.Lock()
        self._pool: deque[str] = deque()
        self._burned: list[str] = []
        for p in proxy_list:
            p = p.strip()
            if p and not p.startswith("#"):
                if not p.startswith(("http://", "https://", "socks5://", "socks4://")):
                    p = "http://" + p
                self._pool.append(p)

    def get_proxies(self) -> dict | None:
        """Return the next proxy as a requests-compatible dict (rotates on each call)."""
        with self._lock:
            if not self._pool:
                return None
            p = self._pool[0]
            self._pool.rotate(-1)
            return {"http": p, "https": p}

    def burn(self, proxy_url: str) -> None:
        """Remove a blocked proxy from rotation."""
        with self._lock:
            try:
                self._pool.remove(proxy_url)
            except ValueError:
                pass
            if proxy_url not in self._burned:
                self._burned.append(proxy_url)

    @property
    def active(self) -> int:
        with self._lock:
            return len(self._pool)

    @property
    def burned_count(self) -> int:
        with self._lock:
            return len(self._burned)

    def has_proxies(self) -> bool:
        with self._lock:
            return bool(self._pool)

    def peek(self) -> str | None:
        """Return the current proxy URL without rotating (for display only)."""
        with self._lock:
            return self._pool[0] if self._pool else None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/123.0.0.0 Safari/537.36",
]

FILE_EXTENSIONS = {
    "Documents": {
        "pdf": "PDF",
        "doc": "DOC",
        "docx": "DOCX",
        "odt": "ODT",
        "rtf": "RTF",
        "txt": "TXT",
        "tex": "LaTeX",
        "epub": "EPUB",
        "mobi": "MOBI",
        "azw3": "AZW3",
        "djvu": "DjVu",
    },
    "Spreadsheets": {
        "xls": "XLS",
        "xlsx": "XLSX",
        "csv": "CSV",
        "ods": "ODS",
    },
    "Presentations": {
        "ppt": "PPT",
        "pptx": "PPTX",
        "odp": "ODP",
        "key": "Keynote",
    },
    "Archives": {
        "zip": "ZIP",
        "rar": "RAR",
        "7z": "7-Zip",
        "tar": "TAR",
        "gz": "GZ",
        "bz2": "BZ2",
    },
    "Audio": {
        "mp3": "MP3",
        "flac": "FLAC",
        "wav": "WAV",
        "ogg": "OGG",
        "aac": "AAC",
        "m4a": "M4A",
    },
    "Video": {
        "mp4": "MP4",
        "mkv": "MKV",
        "avi": "AVI",
        "mov": "MOV",
        "wmv": "WMV",
        "webm": "WebM",
    },
    "Data / Code": {
        "sql": "SQL",
        "json": "JSON",
        "xml": "XML",
        "yaml": "YAML",
        "py": "Python",
        "sh": "Shell",
        "js": "JavaScript",
    },
    "Images / Other": {
        "iso": "ISO",
        "apk": "APK",
        "exe": "EXE",
        "dmg": "DMG",
        "torrent": "Torrent",
    },
    "ECU Tunes": {
        "hpt": "HP Tuners",
        "hpl": "HP Tuners Log",
        "ctz": "EFILive",
        "e2p": "EFILive E2P",
        "e2s": "EFILive E2S",
        "tun": "SCT / Generic Tune",
        "bin": "ROM Binary",
        "hex": "Intel HEX ROM",
        "xdf": "TunerPro XDF",
        "adx": "TunerPro Log",
        "fpk": "Hondata FlashPro",
        "otf": "COBB Accessport",
        "rdt": "ECUTek",
        "htf": "Haltech",
        "pclr": "Link ECU",
        "pcl": "Link ECU (PCL)",
        "cal": "AEM Calibration",
        "ecm": "ECUMaster",
        "map": "WinOLS Map",
        "ols": "WinOLS Project",
        "a2l": "ASAP2 / INCA",
        "frf": "OEM Flash File",
    },
}

# Flat list of all extensions for easy lookups
ALL_EXTENSIONS = {ext for group in FILE_EXTENSIONS.values() for ext in group}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_BLOCK_CODES    = {429, 403, 503, 999}
_BLOCK_SNIPPETS = ("captcha", "robot", "unusual traffic", "rate limit",
                   "too many requests", "access denied", "blocked")


def _check_response(r: "requests.Response", engine: str) -> None:
    """Raise RateLimitedError if the response looks like a block/rate-limit."""
    if r.status_code in _BLOCK_CODES:
        raise RateLimitedError(f"{engine} returned HTTP {r.status_code}")
    body_low = r.text[:2000].lower()
    if any(phrase in body_low for phrase in _BLOCK_SNIPPETS):
        raise RateLimitedError(f"{engine} returned a CAPTCHA/block page")


def get_headers(referer: str = "https://www.google.com") -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        # Do NOT set Accept-Encoding — let requests handle it so it only
        # advertises encodings it can actually decompress (avoids brotli garbage)
        "Referer": referer,
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def is_direct_link(url: str, filetypes: list[str]) -> bool:
    """Return True when the URL path ends with one of the requested extensions."""
    path = urlparse(url).path.lower().split("?")[0]
    return any(path.endswith(f".{ft}") for ft in filetypes)


def make_result(url: str, title: str, snippet: str, engine: str, filetype: str) -> dict:
    filename = unquote(urlparse(url).path.split("/")[-1]) or url
    return {
        "url": url,
        "title": title or filename,
        "snippet": snippet,
        "engine": engine,
        "filetype": filetype.upper(),
        "filename": filename,
        "domain": urlparse(url).netloc,
    }


def make_page_hit(url: str, title: str, snippet: str, engine: str, filetype: str) -> dict:
    """A search-result page that *mentions* the filetype but isn't a direct link."""
    return {
        "page_url": url,
        "title": title,
        "snippet": snippet,
        "engine": engine,
        "filetype": filetype,
    }


def _proxy_display(proxy_manager: "ProxyManager | None") -> str | None:
    """Return a short display string for the current proxy (host:port only)."""
    if not proxy_manager:
        return None
    raw = proxy_manager.peek()
    if not raw:
        return None
    try:
        p = urlparse(raw)
        return f"{p.hostname}:{p.port}"
    except Exception:
        return raw


def jitter(lo: float = 0.4, hi: float = 1.2) -> None:
    time.sleep(random.uniform(lo, hi))


def _req(
    method: str,
    url: str,
    proxy_manager: "ProxyManager | None" = None,
    engine: str = "",
    **kwargs,
) -> "requests.Response":
    """HTTP request with optional proxy rotation.

    Automatically calls _check_response(). On a rate-limit hit, burns the
    current proxy and retries once with the next one (if available).
    Non-rate-limit exceptions propagate immediately.
    """
    max_attempts = 2 if (proxy_manager and proxy_manager.has_proxies()) else 1

    for attempt in range(max_attempts):
        proxies = proxy_manager.get_proxies() if proxy_manager else None
        proxy_url = next(iter(proxies.values())) if proxies else None
        try:
            r = requests.request(method, url, proxies=proxies, **kwargs)
            _check_response(r, engine)
            return r
        except RateLimitedError:
            if proxy_url and proxy_manager:
                proxy_manager.burn(proxy_url)
            if attempt == max_attempts - 1:
                raise
        except Exception:
            raise

    raise RateLimitedError(f"{engine} blocked on all available proxies")


# ---------------------------------------------------------------------------
# Page crawler – visits search-result pages to find actual file links
# ---------------------------------------------------------------------------

# Skip domains that are search engines themselves or unlikely to host files
_SKIP_DOMAINS = {
    "google.com", "bing.com", "duckduckgo.com", "yahoo.com",
    "youtube.com", "facebook.com", "twitter.com", "x.com",
    "reddit.com", "instagram.com", "linkedin.com", "tiktok.com",
    "amazon.com", "ebay.com", "wikipedia.org",
}


def _should_skip_domain(domain: str) -> bool:
    domain = domain.lower().lstrip("www.")
    return any(domain == s or domain.endswith("." + s) for s in _SKIP_DOMAINS)


def crawl_page_for_links(
    page_url: str,
    filetypes: list[str],
    page_title: str = "",
    page_snippet: str = "",
    engine: str = "",
    proxy_manager: "ProxyManager | None" = None,
) -> list[dict]:
    """Visit a single page and extract all <a href> links that end with a target extension."""
    results = []
    try:
        parsed = urlparse(page_url)
        if _should_skip_domain(parsed.netloc):
            return results

        proxies = proxy_manager.get_proxies() if proxy_manager else None
        r = requests.get(
            page_url,
            headers=get_headers(referer=f"{parsed.scheme}://{parsed.netloc}"),
            timeout=10,
            allow_redirects=True,
            stream=True,
            proxies=proxies,
        )

        # Check content-type — only parse HTML
        ct = r.headers.get("Content-Type", "").lower()
        if "html" not in ct and "xhtml" not in ct:
            # The page itself might be a direct download
            if is_direct_link(r.url, filetypes):
                ext = r.url.rsplit(".", 1)[-1].lower().split("?")[0]
                results.append(make_result(r.url, page_title, page_snippet, engine, ext))
            return results

        # Limit how much HTML we parse (first 500KB)
        body = r.text[:512_000]
        soup = BeautifulSoup(body, "html.parser")
        seen = set()

        base_url = page_url
        base_tag = soup.find("base")
        if base_tag and base_tag.get("href"):
            base_url = urljoin(page_url, base_tag["href"])

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue

            full_url = urljoin(base_url, href)

            # Strip fragments
            full_url = full_url.split("#")[0]

            if full_url in seen:
                continue

            if is_direct_link(full_url, filetypes):
                seen.add(full_url)
                ext = full_url.rsplit(".", 1)[-1].lower().split("?")[0]
                link_text = a.get_text(strip=True)[:120] or ""
                results.append(
                    make_result(
                        full_url,
                        link_text or page_title,
                        f"Found on {parsed.netloc}",
                        engine + " (crawled)",
                        ext,
                    )
                )
    except Exception:
        pass

    return results


# ---------------------------------------------------------------------------
# Search engines
# ---------------------------------------------------------------------------


def search_bing(query: str, filetypes: list[str], max_results: int = 60, proxy_manager: "ProxyManager | None" = None, progress_q: "queue.SimpleQueue | None" = None) -> tuple[list[dict], list[dict]]:
    results, pages, seen = [], [], set()
    per_type = max(10, max_results // max(len(filetypes), 1))

    for ft in filetypes:
        dork = f'filetype:{ft} {query}'
        page_count = min(5, (per_type + 9) // 10)

        for page in range(page_count):
            try:
                if progress_q:
                    progress_q.put_nowait({"type": "engine_progress", "engine": "Bing",
                        "filetype": ft, "page": page + 1, "pages": page_count,
                        "proxy": _proxy_display(proxy_manager)})
                params = {"q": dork, "first": page * 10 + 1, "count": 10}
                r = _req(
                    "GET",
                    "https://www.bing.com/search",
                    proxy_manager=proxy_manager,
                    engine="Bing",
                    params=params,
                    headers=get_headers("https://www.bing.com"),
                    timeout=12,
                )
                if r.status_code != 200:
                    break

                soup = BeautifulSoup(r.text, "html.parser")
                found = 0

                for tag in soup.select("li.b_algo"):
                    a = tag.select_one("h2 a") or tag.select_one("a")
                    if not a:
                        continue
                    href = a.get("href", "")
                    if not href.startswith("http"):
                        continue
                    if href in seen:
                        continue
                    seen.add(href)
                    title = a.get_text(strip=True)
                    snippet_el = tag.select_one(".b_caption p") or tag.select_one("p")
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                    if is_direct_link(href, [ft]):
                        results.append(make_result(href, title, snippet, "Bing", ft))
                    else:
                        # Also try data-href
                        alt = a.get("data-href", "")
                        if alt and is_direct_link(alt, [ft]):
                            results.append(make_result(alt, title, snippet, "Bing", ft))
                        else:
                            pages.append(make_page_hit(href, title, snippet, "Bing", ft))
                    found += 1

                if found == 0:
                    break
                jitter()
            except RateLimitedError:
                raise  # propagate so the route can report it
            except Exception:
                break

    return results, pages


def search_duckduckgo(query: str, filetypes: list[str], max_results: int = 60, proxy_manager: "ProxyManager | None" = None, progress_q: "queue.SimpleQueue | None" = None) -> tuple[list[dict], list[dict]]:
    results, pages, seen = [], [], set()

    for ft in filetypes:
        dork = f'filetype:{ft} {query}'
        try:
            if progress_q:
                progress_q.put_nowait({"type": "engine_progress", "engine": "DuckDuckGo",
                    "filetype": ft, "page": 1, "pages": 1,
                    "proxy": _proxy_display(proxy_manager)})
            # POST to the HTML lite endpoint (GET ignores the data= body)
            r = _req(
                "POST",
                "https://html.duckduckgo.com/html/",
                proxy_manager=proxy_manager,
                engine="DuckDuckGo",
                data={"q": dork, "b": "", "kl": "us-en"},
                headers={**get_headers("https://duckduckgo.com"),
                         "Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            for result in soup.select(".result"):
                a = result.select_one(".result__title a") or result.select_one("a.result__url")
                if not a:
                    continue
                href = a.get("href", "")
                if "uddg=" in href:
                    m2 = re.search(r'uddg=([^&]+)', href)
                    if m2:
                        href = unquote(m2.group(1))
                if not href.startswith("http"):
                    continue
                if href in seen:
                    continue
                seen.add(href)
                title = a.get_text(strip=True)
                snippet_el = result.select_one(".result__snippet")
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                if is_direct_link(href, [ft]):
                    results.append(make_result(href, title, snippet, "DuckDuckGo", ft))
                else:
                    pages.append(make_page_hit(href, title, snippet, "DuckDuckGo", ft))
            jitter(1.0, 2.5)
        except RateLimitedError:
            raise
        except Exception:
            continue

    return results, pages


def search_yahoo(query: str, filetypes: list[str], max_results: int = 60, proxy_manager: "ProxyManager | None" = None, progress_q: "queue.SimpleQueue | None" = None) -> tuple[list[dict], list[dict]]:
    results, page_hits, seen = [], [], set()
    per_type = max(10, max_results // max(len(filetypes), 1))

    for ft in filetypes:
        dork = f'filetype:{ft} {query}'
        page_count = min(4, (per_type + 9) // 10)

        for page in range(page_count):
            try:
                if progress_q:
                    progress_q.put_nowait({"type": "engine_progress", "engine": "Yahoo",
                        "filetype": ft, "page": page + 1, "pages": page_count,
                        "proxy": _proxy_display(proxy_manager)})
                params = {"p": dork, "b": page * 10 + 1, "pz": 10}
                r = _req(
                    "GET",
                    "https://search.yahoo.com/search",
                    proxy_manager=proxy_manager,
                    engine="Yahoo",
                    params=params,
                    headers=get_headers("https://search.yahoo.com"),
                    timeout=12,
                )
                if r.status_code != 200:
                    break

                soup = BeautifulSoup(r.text, "html.parser")
                found = 0

                # Yahoo changes its HTML structure frequently — try multiple
                # selector strategies from most to least specific
                containers = (
                    soup.select("div.algo") or
                    soup.select("div[class*='algo']") or
                    soup.select("ol#web li") or
                    soup.select("li.first") or
                    []
                )
                containers += soup.select("div.dd")  # always include dd results

                for tag in containers:
                    a = (
                        tag.select_one("h3.title a") or
                        tag.select_one(".compTitle a") or
                        tag.select_one("h3 a") or
                        tag.select_one("a[href*='r.search.yahoo.com']") or
                        tag.select_one("a[href^='http']")
                    )
                    if not a:
                        continue
                    href = a.get("href", "")

                    # Unwrap Yahoo redirect URLs (multiple known formats)
                    if "/RU=" in href:
                        m = re.search(r"/RU=([^/]+)/", href)
                        if m:
                            href = unquote(m.group(1))
                    elif "r.search.yahoo.com" in href:
                        for param in ("RU", "u", "url"):
                            m = re.search(rf'[?&/]{re.escape(param)}=([^&/]+)', href)
                            if m:
                                decoded = unquote(m.group(1))
                                if decoded.startswith("http"):
                                    href = decoded
                                    break

                    if not href.startswith("http"):
                        continue
                    if href in seen:
                        continue
                    seen.add(href)
                    title = a.get_text(strip=True)
                    snippet_el = (
                        tag.select_one(".compText") or
                        tag.select_one(".st") or
                        tag.select_one("p")
                    )
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                    if is_direct_link(href, [ft]):
                        results.append(make_result(href, title, snippet, "Yahoo", ft))
                    else:
                        page_hits.append(make_page_hit(href, title, snippet, "Yahoo", ft))
                    found += 1

                if found == 0:
                    break
                jitter()
            except RateLimitedError:
                raise
            except Exception:
                break

    return results, page_hits


def search_google_cse(
    query: str, filetypes: list[str], api_key: str, cx: str, max_results: int = 100
) -> tuple[list[dict], list[dict]]:
    """Google Custom Search Engine API (requires free API key + CX)."""
    results, page_hits, seen = [], [], set()

    for ft in filetypes:
        start = 1
        while start <= min(max_results, 100):
            try:
                params = {
                    "key": api_key,
                    "cx": cx,
                    "q": f"{query} filetype:{ft}",
                    "start": start,
                    "num": 10,
                }
                r = requests.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params=params,
                    timeout=12,
                )
                if r.status_code != 200:
                    break
                data = r.json()
                items = data.get("items", [])
                if not items:
                    break

                for item in items:
                    url = item.get("link", "")
                    if url and url not in seen:
                        seen.add(url)
                        title = item.get("title", "")
                        snippet = item.get("snippet", "")
                        if is_direct_link(url, [ft]):
                            results.append(make_result(url, title, snippet, "Google CSE", ft))
                        else:
                            page_hits.append(make_page_hit(url, title, snippet, "Google CSE", ft))
                start += 10
                jitter(0.2, 0.5)
            except Exception:
                break

    return results, page_hits


def search_commoncrawl(query: str, filetypes: list[str], max_results: int = 100) -> tuple[list[dict], list[dict]]:
    """Query the Common Crawl URL Index API for publicly crawled files."""
    results, seen = [], set()  # CC returns direct URLs only — no pages to crawl

    try:
        idx_r = requests.get("https://index.commoncrawl.org/collinfo.json", timeout=10)
        indexes = [entry["cdx-api"] for entry in idx_r.json()[:3]]
    except Exception:
        # Fallback — keep updated; check index.commoncrawl.org/collinfo.json
        indexes = [
            "https://index.commoncrawl.org/CC-MAIN-2026-12-index",
            "https://index.commoncrawl.org/CC-MAIN-2026-08-index",
        ]

    per_type = max(10, max_results // max(len(filetypes), 1))

    for ft in filetypes:
        for idx_url in indexes[:2]:
            try:
                params = {
                    "url": f"*.{ft}",
                    "output": "json",
                    "limit": per_type,
                    "fl": "url,status,timestamp,mime",
                    "filter": "status:200",   # no leading '=' — CDX API syntax
                }
                r = requests.get(idx_url, params=params, timeout=25)
                if r.status_code != 200:
                    continue

                for line in r.text.strip().splitlines():
                    try:
                        entry = json.loads(line)
                        url = entry.get("url", "")
                        if url and url not in seen and is_direct_link(url, [ft]):
                            seen.add(url)
                            ts = entry.get("timestamp", "")[:8]
                            results.append(
                                make_result(
                                    url,
                                    url.split("/")[-1],
                                    f"Common Crawl index · crawled {ts}",
                                    "Common Crawl",
                                    ft,
                                )
                            )
                    except Exception:
                        continue
                jitter(0.3, 0.8)
            except Exception:
                continue

    return results, []  # CC only returns direct URLs


def search_archive_org(query: str, filetypes: list[str], max_results: int = 60) -> tuple[list[dict], list[dict]]:
    """Search the Internet Archive for publicly available files."""
    results, page_hits, seen = [], [], set()

    MEDIA_MAP = {
        "pdf": ("texts", "PDF"),
        "epub": ("texts", "EPUB"),
        "mobi": ("texts", "MOBI"),
        "txt": ("texts", "DjVuTXT"),
        "mp3": ("audio", "MP3"),
        "flac": ("audio", "Flac"),
        "ogg": ("audio", "Ogg Vorbis"),
        "mp4": ("movies", "MPEG4"),
        "avi": ("movies", "AVI"),
        "mkv": ("movies", "Matroska"),
        "zip": ("data", "ZIP"),
        "csv": ("data", "COMMA-SEPARATED VALUES"),
    }

    for ft in filetypes:
        if ft not in MEDIA_MAP:
            continue
        mediatype, fmt = MEDIA_MAP[ft]

        try:
            params = {
                "q": f"{query} format:{fmt}",
                "fl[]": ["identifier", "title"],
                "rows": min(max_results, 50),
                "page": 1,
                "output": "json",
                "mediatype": mediatype,
            }
            r = requests.get(
                "https://archive.org/advancedsearch.php",
                params=params,
                headers={"User-Agent": random.choice(USER_AGENTS)},
                timeout=15,
            )
            if r.status_code != 200:
                continue

            docs = r.json().get("response", {}).get("docs", [])
            for doc in docs:
                ident = doc.get("identifier", "")
                if not ident:
                    continue
                # The /download/ listing page has all the actual file links —
                # send it to the crawler so Phase 2 finds the real .ft file
                listing_url = f"https://archive.org/download/{ident}/"
                item_url    = f"https://archive.org/details/{ident}"
                if listing_url not in seen:
                    seen.add(listing_url)
                    page_hits.append(
                        make_page_hit(
                            listing_url,
                            doc.get("title", ident),
                            f"Internet Archive — {fmt}",
                            "Internet Archive",
                            ft,
                        )
                    )
            jitter(0.5, 1.0)
        except Exception:
            continue

    return results, page_hits  # let the crawler find exact file URLs


def search_searxng(
    query: str, filetypes: list[str], instance_url: str, max_results: int = 60
) -> tuple[list[dict], list[dict]]:
    """Search via a self-hosted or public SearXNG instance (JSON API)."""
    results, page_hits, seen = [], [], set()
    instance_url = instance_url.rstrip("/")

    for ft in filetypes:
        dork = f'filetype:{ft} {query}'
        try:
            params = {
                "q": dork,
                "format": "json",
                "engines": "google,bing,duckduckgo,yahoo",
                "categories": "general",
                "language": "en-US",
            }
            r = requests.get(
                f"{instance_url}/search",
                params=params,
                headers=get_headers(instance_url),
                timeout=20,
            )
            if r.status_code != 200:
                continue

            for item in r.json().get("results", []):
                url = item.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    title = item.get("title", "")
                    content = item.get("content", "")
                    if is_direct_link(url, [ft]):
                        results.append(make_result(url, title, content, "SearXNG", ft))
                    else:
                        page_hits.append(make_page_hit(url, title, content, "SearXNG", ft))
            jitter(1.0, 2.0)
        except Exception:
            continue

    return results, page_hits


def search_startpage(query: str, filetypes: list[str], max_results: int = 40, proxy_manager: "ProxyManager | None" = None, progress_q: "queue.SimpleQueue | None" = None) -> tuple[list[dict], list[dict]]:
    """Search Startpage (Google proxy) for files."""
    results, page_hits, seen = [], [], set()

    for ft in filetypes:
        dork = f'filetype:{ft} {query}'
        try:
            if progress_q:
                progress_q.put_nowait({"type": "engine_progress", "engine": "Startpage",
                    "filetype": ft, "page": 1, "pages": 1,
                    "proxy": _proxy_display(proxy_manager)})
            params = {"q": dork, "language": "english", "cat": "web"}
            r = _req(
                "GET",
                "https://www.startpage.com/sp/search",
                proxy_manager=proxy_manager,
                engine="Startpage",
                params=params,
                headers=get_headers("https://www.startpage.com"),
                timeout=15,
            )
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup.select(".result-container, .w-gl__result"):
                a = tag.select_one("a.result-title, a.w-gl__result-title, h3 a")
                if not a:
                    continue
                href = a.get("href", "")
                if not href.startswith("http"):
                    continue
                if href in seen:
                    continue
                seen.add(href)
                title = a.get_text(strip=True)
                snippet_el = tag.select_one(".result-description, .w-gl__description")
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                if is_direct_link(href, [ft]):
                    results.append(make_result(href, title, snippet, "Startpage", ft))
                else:
                    page_hits.append(make_page_hit(href, title, snippet, "Startpage", ft))
            jitter(1.5, 3.0)
        except Exception:
            continue

    return results, page_hits


def search_mojeek(query: str, filetypes: list[str], max_results: int = 40, proxy_manager: "ProxyManager | None" = None, progress_q: "queue.SimpleQueue | None" = None) -> tuple[list[dict], list[dict]]:
    """Search Mojeek (independent index)."""
    results, page_hits, seen = [], [], set()

    for ft in filetypes:
        dork = f'filetype:{ft} {query}'
        try:
            if progress_q:
                progress_q.put_nowait({"type": "engine_progress", "engine": "Mojeek",
                    "filetype": ft, "page": 1, "pages": 1,
                    "proxy": _proxy_display(proxy_manager)})
            params = {"q": dork, "fmt": "10"}
            r = _req(
                "GET",
                "https://www.mojeek.com/search",
                proxy_manager=proxy_manager,
                engine="Mojeek",
                params=params,
                headers=get_headers("https://www.mojeek.com"),
                timeout=12,
            )
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup.select("ul.results-standard li"):
                a = tag.select_one("a.ob")
                if not a:
                    continue
                href = a.get("href", "")
                if not href.startswith("http"):
                    continue
                if href in seen:
                    continue
                seen.add(href)
                title_el = tag.select_one(".title")
                desc_el = tag.select_one(".s")
                title = title_el.get_text(strip=True) if title_el else ""
                desc = desc_el.get_text(strip=True) if desc_el else ""

                if is_direct_link(href, [ft]):
                    results.append(make_result(href, title, desc, "Mojeek", ft))
                else:
                    page_hits.append(make_page_hit(href, title, desc, "Mojeek", ft))
            jitter(0.8, 1.5)
        except Exception:
            continue

    return results, page_hits


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/favicon.ico")
def favicon():
    return app.send_static_file("favicon.ico")


@app.route("/")
def index():
    return render_template("index.html", file_extensions=FILE_EXTENSIONS)


_PROXY_SOURCES = {
    "http":   "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "socks4": "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks4.txt",
    "socks5": "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",
}


@app.route("/fetch-proxies")
def fetch_proxies():
    """Fetch a fresh proxy list from TheSpeedX/SOCKS-List and return as JSON."""
    kind = request.args.get("type", "http").lower()
    if kind not in _PROXY_SOURCES:
        return jsonify({"error": f"Unknown type '{kind}'. Use: http, socks4, socks5"}), 400

    url = _PROXY_SOURCES[kind]
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": random.choice(USER_AGENTS)})
        if r.status_code != 200:
            return jsonify({"error": f"Upstream returned HTTP {r.status_code}"}), 502

        scheme = "socks4://" if kind == "socks4" else ("socks5://" if kind == "socks5" else "http://")
        proxies = []
        for line in r.text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                proxies.append(f"{scheme}{line}" if "://" not in line else line)

        return jsonify({"proxies": proxies, "count": len(proxies), "type": kind})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


def _sse(payload: dict) -> str:
    """Format a dict as a single SSE data line."""
    return f"data: {json.dumps(payload)}\n\n"


# HTTP status codes that indicate rate-limiting / blocking
_RATELIMIT_CODES = {429, 403, 503, 999}  # 999 = Yahoo's soft-block code
_RATELIMIT_PHRASES = ("rate limit", "too many requests", "blocked", "captcha",
                      "unusual traffic", "access denied", "robot", "automated")


def _looks_rate_limited(exc: Exception) -> bool:
    if isinstance(exc, RateLimitedError):
        return True
    msg = str(exc).lower()
    if any(p in msg for p in _RATELIMIT_PHRASES):
        return True
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", 0) in _RATELIMIT_CODES:
        return True
    return False


@app.route("/search", methods=["POST"])
def search():
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    filetypes = [ft.lower() for ft in data.get("filetypes", []) if ft.lower() in ALL_EXTENSIONS]
    engines = data.get("engines", [])
    max_results = min(int(data.get("max_results", 60)), 200)

    # Optional credentials – extract before generator to avoid request-context issues
    google_api_key = data.get("google_api_key", "").strip()
    google_cx = data.get("google_cx", "").strip()
    searxng_url = data.get("searxng_url", "").strip()

    # Optional proxy list
    raw_proxies = [p.strip() for p in data.get("proxies", []) if isinstance(p, str) and p.strip()]
    proxy_manager: ProxyManager | None = ProxyManager(raw_proxies) if raw_proxies else None

    # Validate before opening the stream
    if not query:
        return jsonify({"error": "Query is required"}), 400
    if not filetypes:
        return jsonify({"error": "Select at least one file type"}), 400
    if not engines:
        return jsonify({"error": "Select at least one search engine"}), 400

    pm = proxy_manager  # short alias for lambda capture
    pq: queue.SimpleQueue = queue.SimpleQueue()  # live progress events from engine threads

    TASKS = {
        "bing":        lambda: search_bing(query, filetypes, max_results, pm, pq),
        "duckduckgo":  lambda: search_duckduckgo(query, filetypes, max_results, pm, pq),
        "yahoo":       lambda: search_yahoo(query, filetypes, max_results, pm, pq),
        "startpage":   lambda: search_startpage(query, filetypes, max_results, pm, pq),
        "mojeek":      lambda: search_mojeek(query, filetypes, max_results, pm, pq),
        "commoncrawl": lambda: search_commoncrawl(query, filetypes, max_results),
        "archive":     lambda: search_archive_org(query, filetypes, max_results),
    }
    if "google" in engines and google_api_key and google_cx:
        TASKS["google"] = lambda: search_google_cse(query, filetypes, google_api_key, google_cx, max_results)
    if "searxng" in engines and searxng_url:
        TASKS["searxng"] = lambda: search_searxng(query, filetypes, searxng_url, max_results)

    active = {k: v for k, v in TASKS.items() if k in engines}

    deep_crawl = data.get("deep_crawl", True)
    max_crawl = min(int(data.get("max_crawl", 80)), 200)

    @stream_with_context
    def generate():
        seen_urls: set[str] = set()
        total = 0
        all_pages: list[dict] = []  # pages to crawl in phase 2

        # ── Phase 1: search engines ──────────────────────────────────────
        yield _sse({"type": "engines_registered", "engines": list(active.keys())})

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(active) or 1) as pool:
            future_to_engine = {pool.submit(fn): eng for eng, fn in active.items()}

            for eng in active:
                yield _sse({"type": "engine_start", "engine": eng, "ts": time.time()})

            def _proxy_info():
                return (
                    {"proxies_active": proxy_manager.active,
                     "proxies_burned": proxy_manager.burned_count}
                    if proxy_manager else {}
                )

            def _drain_progress():
                """Yield all pending progress events from the queue."""
                while True:
                    try:
                        yield _sse(pq.get_nowait())
                    except queue.Empty:
                        break

            def _emit_engine_result(future, eng):
                nonlocal total
                try:
                    direct_results, page_hits = future.result(timeout=5)
                    new_results = []
                    for r in direct_results:
                        url = r["url"]
                        if url not in seen_urls:
                            seen_urls.add(url)
                            new_results.append(r)
                            total += 1

                    page_count = 0
                    for p in page_hits:
                        if p["page_url"] not in seen_urls:
                            all_pages.append(p)
                            page_count += 1

                    return _sse({
                        "type":    "engine_done",
                        "engine":  eng,
                        "count":   len(new_results),
                        "pages":   page_count,
                        "results": new_results,
                        "total":   total,
                        **_proxy_info(),
                    })
                except Exception as exc:
                    return _sse({
                        "type":        "engine_error",
                        "engine":      eng,
                        "error":       str(exc),
                        "rate_limited": _looks_rate_limited(exc),
                        "total":       total,
                        **_proxy_info(),
                    })

            # Poll the progress queue every 500 ms while waiting for engines
            deadline = time.time() + 120
            remaining = set(future_to_engine.keys())
            while remaining:
                yield from _drain_progress()

                time_left = deadline - time.time()
                if time_left <= 0:
                    for future in remaining:
                        future.cancel()
                        yield _sse({"type": "engine_error",
                                    "engine": future_to_engine[future],
                                    "error": "Timed out", "rate_limited": False,
                                    "total": total, **_proxy_info()})
                    remaining.clear()
                    break

                done, _ = concurrent.futures.wait(
                    remaining, timeout=min(0.5, time_left),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    remaining.discard(future)
                    yield _emit_engine_result(future, future_to_engine[future])

            yield from _drain_progress()  # final flush

        # ── Phase 2: crawl result pages for actual file links ────────────
        if deep_crawl and all_pages:
            # Deduplicate + limit pages to crawl
            crawl_seen = set()
            crawl_queue = []
            for p in all_pages:
                u = p["page_url"]
                if u not in crawl_seen:
                    crawl_seen.add(u)
                    crawl_queue.append(p)
                if len(crawl_queue) >= max_crawl:
                    break

            yield _sse({
                "type":       "crawl_start",
                "page_count": len(crawl_queue),
            })

            crawled = 0
            batch_results: list[dict] = []

            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as cpool:
                future_to_page = {
                    cpool.submit(
                        crawl_page_for_links,
                        p["page_url"],
                        filetypes,
                        p.get("title", ""),
                        p.get("snippet", ""),
                        p.get("engine", ""),
                        proxy_manager,
                    ): p
                    for p in crawl_queue
                }

                def _process_crawl_future(future):
                    nonlocal total
                    try:
                        links = future.result(timeout=12)
                        for r in links:
                            url = r["url"]
                            if url not in seen_urls:
                                seen_urls.add(url)
                                batch_results.append(r)
                                total += 1
                    except Exception:
                        pass

                try:
                    for future in concurrent.futures.as_completed(future_to_page, timeout=120):
                        crawled += 1
                        _process_crawl_future(future)
                        if batch_results and (crawled % 3 == 0 or crawled == len(crawl_queue)):
                            yield _sse({
                                "type":     "crawl_progress",
                                "crawled":  crawled,
                                "of":       len(crawl_queue),
                                "results":  batch_results,
                                "total":    total,
                            })
                            batch_results = []
                except TimeoutError:
                    # Flush whatever we have and move on
                    crawled = len(crawl_queue)  # mark as done
                    if batch_results:
                        yield _sse({
                            "type":     "crawl_progress",
                            "crawled":  crawled,
                            "of":       len(crawl_queue),
                            "results":  batch_results,
                            "total":    total,
                        })
                        batch_results = []

            # Flush any remaining results
            if batch_results:
                yield _sse({
                    "type":     "crawl_progress",
                    "crawled":  crawled,
                    "of":       len(crawl_queue),
                    "results":  batch_results,
                    "total":    total,
                })

            yield _sse({"type": "crawl_done", "crawled": crawled, "total": total})

        yield _sse({"type": "done", "total": total})

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering if behind a proxy
        },
    )


if __name__ == "__main__":
    import sys
    # Disable the Werkzeug reloader on Windows — it causes WinError 10038
    # (socket operation on non-socket) when reloading during a live SSE stream.
    use_reloader = sys.platform != "win32"
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=use_reloader)
