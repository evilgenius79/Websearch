"""
Multi-Engine File Search Tool
Searches multiple search engines simultaneously for direct file download links
using filetype dork queries.
"""

import concurrent.futures
import json
import random
import re
import time
from urllib.parse import urlencode, urlparse, unquote

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

app = Flask(__name__)


class RateLimitedError(Exception):
    """Raised when a search engine returns a rate-limit / CAPTCHA response."""

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
    "eBooks": {
        "epub": "EPUB",
        "mobi": "MOBI",
        "azw3": "AZW3",
        "djvu": "DjVu",
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
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": referer,
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
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


def jitter(lo: float = 0.4, hi: float = 1.2) -> None:
    time.sleep(random.uniform(lo, hi))


# ---------------------------------------------------------------------------
# Search engines
# ---------------------------------------------------------------------------


def search_bing(query: str, filetypes: list[str], max_results: int = 60) -> list[dict]:
    results, seen = [], set()
    per_type = max(10, max_results // max(len(filetypes), 1))

    for ft in filetypes:
        dork = f'filetype:{ft} {query}'
        pages = min(5, (per_type + 9) // 10)

        for page in range(pages):
            try:
                params = {"q": dork, "first": page * 10 + 1, "count": 10}
                r = requests.get(
                    "https://www.bing.com/search",
                    params=params,
                    headers=get_headers("https://www.bing.com"),
                    timeout=12,
                )
                _check_response(r, "Bing")
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
                    if not is_direct_link(href, [ft]):
                        # Sometimes Bing caches the real URL in data-href
                        href = a.get("data-href", href)
                        if not is_direct_link(href, [ft]):
                            continue
                    seen.add(href)
                    snippet_el = tag.select_one(".b_caption p") or tag.select_one("p")
                    results.append(
                        make_result(
                            href,
                            a.get_text(strip=True),
                            snippet_el.get_text(strip=True) if snippet_el else "",
                            "Bing",
                            ft,
                        )
                    )
                    found += 1

                if found == 0:
                    break
                jitter()
            except Exception:
                break

    return results


def search_duckduckgo(query: str, filetypes: list[str], max_results: int = 60) -> list[dict]:
    results, seen = [], set()

    for ft in filetypes:
        dork = f'filetype:{ft} {query}'
        try:
            session = requests.Session()
            # Get vqd token first (required by DDG)
            r0 = session.get(
                "https://duckduckgo.com/",
                params={"q": dork},
                headers=get_headers("https://duckduckgo.com"),
                timeout=12,
            )
            vqd = ""
            m = re.search(r'vqd=([\d-]+)', r0.text)
            if m:
                vqd = m.group(1)

            # HTML lite version is more scrapable
            r = session.get(
                "https://html.duckduckgo.com/html/",
                data={"q": dork, "b": "", "kl": "us-en"},
                headers={**get_headers("https://duckduckgo.com"), "Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            _check_response(r, "DuckDuckGo")
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            for result in soup.select(".result"):
                a = result.select_one(".result__title a") or result.select_one("a.result__url")
                if not a:
                    continue
                href = a.get("href", "")
                # DDG wraps links with a redirect – extract the real URL
                if "uddg=" in href:
                    m2 = re.search(r'uddg=([^&]+)', href)
                    if m2:
                        href = unquote(m2.group(1))
                if not href.startswith("http"):
                    continue
                if href in seen:
                    continue
                if not is_direct_link(href, [ft]):
                    continue
                seen.add(href)
                snippet_el = result.select_one(".result__snippet")
                results.append(
                    make_result(
                        href,
                        a.get_text(strip=True),
                        snippet_el.get_text(strip=True) if snippet_el else "",
                        "DuckDuckGo",
                        ft,
                    )
                )
            jitter(1.0, 2.5)
        except Exception:
            continue

    return results


def search_yahoo(query: str, filetypes: list[str], max_results: int = 60) -> list[dict]:
    results, seen = [], set()
    per_type = max(10, max_results // max(len(filetypes), 1))

    for ft in filetypes:
        dork = f'filetype:{ft} {query}'
        pages = min(4, (per_type + 9) // 10)

        for page in range(pages):
            try:
                params = {"p": dork, "b": page * 10 + 1, "pz": 10}
                r = requests.get(
                    "https://search.yahoo.com/search",
                    params=params,
                    headers=get_headers("https://search.yahoo.com"),
                    timeout=12,
                )
                _check_response(r, "Yahoo")
                if r.status_code != 200:
                    break

                soup = BeautifulSoup(r.text, "html.parser")
                found = 0

                for tag in soup.select("div.algo, div.dd"):
                    a = tag.select_one("h3 a") or tag.select_one("a")
                    if not a:
                        continue
                    href = a.get("href", "")
                    # Yahoo wraps URLs
                    if "/RU=" in href:
                        m = re.search(r"/RU=([^/]+)/", href)
                        if m:
                            href = unquote(m.group(1))
                    if not href.startswith("http"):
                        continue
                    if href in seen:
                        continue
                    if not is_direct_link(href, [ft]):
                        continue
                    seen.add(href)
                    snippet_el = tag.select_one(".compText") or tag.select_one("p")
                    results.append(
                        make_result(
                            href,
                            a.get_text(strip=True),
                            snippet_el.get_text(strip=True) if snippet_el else "",
                            "Yahoo",
                            ft,
                        )
                    )
                    found += 1

                if found == 0:
                    break
                jitter()
            except Exception:
                break

    return results


def search_google_cse(
    query: str, filetypes: list[str], api_key: str, cx: str, max_results: int = 100
) -> list[dict]:
    """Google Custom Search Engine API (requires free API key + CX)."""
    results, seen = [], set()

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
                        results.append(
                            make_result(
                                url,
                                item.get("title", ""),
                                item.get("snippet", ""),
                                "Google CSE",
                                ft,
                            )
                        )
                start += 10
                jitter(0.2, 0.5)
            except Exception:
                break

    return results


def search_commoncrawl(query: str, filetypes: list[str], max_results: int = 100) -> list[dict]:
    """Query the Common Crawl URL Index API for publicly crawled files."""
    results, seen = [], set()

    try:
        idx_r = requests.get("https://index.commoncrawl.org/collinfo.json", timeout=10)
        indexes = [entry["cdx-api"] for entry in idx_r.json()[:3]]
    except Exception:
        indexes = [
            "https://index.commoncrawl.org/CC-MAIN-2024-10-index",
            "https://index.commoncrawl.org/CC-MAIN-2023-50-index",
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
                    "filter": "=status:200",
                }
                r = requests.get(idx_url, params=params, timeout=20)
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

    return results


def search_archive_org(query: str, filetypes: list[str], max_results: int = 60) -> list[dict]:
    """Search the Internet Archive for publicly available files."""
    results, seen = [], set()

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
                headers=get_headers("https://archive.org"),
                timeout=15,
            )
            if r.status_code != 200:
                continue

            docs = r.json().get("response", {}).get("docs", [])
            for doc in docs:
                ident = doc.get("identifier", "")
                if not ident:
                    continue
                # Fetch the item's file listing to get exact direct URL
                meta_url = f"https://archive.org/download/{ident}/{ident}.{ft}"
                if meta_url not in seen:
                    seen.add(meta_url)
                    results.append(
                        make_result(
                            meta_url,
                            doc.get("title", ident),
                            f"Internet Archive — {fmt}",
                            "Internet Archive",
                            ft,
                        )
                    )
            jitter(0.5, 1.0)
        except Exception:
            continue

    return results


def search_searxng(
    query: str, filetypes: list[str], instance_url: str, max_results: int = 60
) -> list[dict]:
    """Search via a self-hosted or public SearXNG instance (JSON API)."""
    results, seen = [], set()
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
                if url and url not in seen and is_direct_link(url, [ft]):
                    seen.add(url)
                    results.append(
                        make_result(
                            url,
                            item.get("title", ""),
                            item.get("content", ""),
                            f"SearXNG",
                            ft,
                        )
                    )
            jitter(1.0, 2.0)
        except Exception:
            continue

    return results


def search_startpage(query: str, filetypes: list[str], max_results: int = 40) -> list[dict]:
    """Search Startpage (Google proxy) for files."""
    results, seen = [], set()

    for ft in filetypes:
        dork = f'filetype:{ft} {query}'
        try:
            params = {"q": dork, "language": "english", "cat": "web"}
            r = requests.get(
                "https://www.startpage.com/sp/search",
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
                if not is_direct_link(href, [ft]):
                    continue
                seen.add(href)
                snippet_el = tag.select_one(".result-description, .w-gl__description")
                results.append(
                    make_result(
                        href,
                        a.get_text(strip=True),
                        snippet_el.get_text(strip=True) if snippet_el else "",
                        "Startpage",
                        ft,
                    )
                )
            jitter(1.5, 3.0)
        except Exception:
            continue

    return results


def search_mojeek(query: str, filetypes: list[str], max_results: int = 40) -> list[dict]:
    """Search Mojeek (independent index)."""
    results, seen = [], set()

    for ft in filetypes:
        dork = f'filetype:{ft} {query}'
        try:
            params = {"q": dork, "fmt": "10"}
            r = requests.get(
                "https://www.mojeek.com/search",
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
                if not is_direct_link(href, [ft]):
                    continue
                seen.add(href)
                title_el = tag.select_one(".title")
                desc_el = tag.select_one(".s")
                results.append(
                    make_result(
                        href,
                        title_el.get_text(strip=True) if title_el else "",
                        desc_el.get_text(strip=True) if desc_el else "",
                        "Mojeek",
                        ft,
                    )
                )
            jitter(0.8, 1.5)
        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html", file_extensions=FILE_EXTENSIONS)


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

    # Validate before opening the stream
    if not query:
        return jsonify({"error": "Query is required"}), 400
    if not filetypes:
        return jsonify({"error": "Select at least one file type"}), 400
    if not engines:
        return jsonify({"error": "Select at least one search engine"}), 400

    TASKS = {
        "bing":        lambda: search_bing(query, filetypes, max_results),
        "duckduckgo":  lambda: search_duckduckgo(query, filetypes, max_results),
        "yahoo":       lambda: search_yahoo(query, filetypes, max_results),
        "startpage":   lambda: search_startpage(query, filetypes, max_results),
        "mojeek":      lambda: search_mojeek(query, filetypes, max_results),
        "commoncrawl": lambda: search_commoncrawl(query, filetypes, max_results),
        "archive":     lambda: search_archive_org(query, filetypes, max_results),
    }
    if "google" in engines and google_api_key and google_cx:
        TASKS["google"] = lambda: search_google_cse(query, filetypes, google_api_key, google_cx, max_results)
    if "searxng" in engines and searxng_url:
        TASKS["searxng"] = lambda: search_searxng(query, filetypes, searxng_url, max_results)

    active = {k: v for k, v in TASKS.items() if k in engines}

    @stream_with_context
    def generate():
        seen_urls: set[str] = set()
        total = 0

        # Tell the client which engines are about to start
        yield _sse({"type": "engines_registered", "engines": list(active.keys())})

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(active) or 1) as pool:
            future_to_engine = {pool.submit(fn): eng for eng, fn in active.items()}

            # Signal each engine as "running" the moment its future is submitted
            for eng in active:
                yield _sse({"type": "engine_start", "engine": eng})

            for future in concurrent.futures.as_completed(future_to_engine, timeout=90):
                eng = future_to_engine[future]
                try:
                    res = future.result(timeout=5)
                    new_results = []
                    for r in res:
                        url = r["url"]
                        if url not in seen_urls:
                            seen_urls.add(url)
                            new_results.append(r)
                            total += 1
                    yield _sse({
                        "type":    "engine_done",
                        "engine":  eng,
                        "count":   len(new_results),
                        "results": new_results,
                        "total":   total,
                    })
                except Exception as exc:
                    err_str = str(exc)
                    yield _sse({
                        "type":        "engine_error",
                        "engine":      eng,
                        "error":       err_str,
                        "rate_limited": _looks_rate_limited(exc),
                        "total":       total,
                    })

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
    app.run(debug=True, host="0.0.0.0", port=5000)
