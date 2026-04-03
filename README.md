# Multi-Engine File Search

> Aggressive two-phase file hunting — search engines + deep page crawling — direct download links only.

A self-hosted web application that fires filetype dork queries at multiple search engines simultaneously, then crawls every result page for actual download links. Built with a Python/Flask streaming backend and a modern dark-themed frontend.

---

## Screenshot

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  🔍 Multi-Engine File Search                                                    │
│  Aggressive parallel search · deep page crawl · direct download links only     │
├──────────────────────┬──────────────────────────────────────────────────────────┤
│  Search Query        │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  Progress      │
│  ─────────────────   │                                                           │
│  Options             │  Searching 3 of 7 engines…        47 links found         │
│  Max results:  60    │  ┌────────────────────────────────────────────────────┐  │
│  Max crawl:    80    │  │ ⠋ Bing           12 direct, 8 pages                │  │
│  [⊙] Deep crawl      │  │ ✓ DuckDuckGo     0 direct, 14 pages                │  │
│                      │  │ ✓ Yahoo          3 direct, 6 pages                 │  │
│  Engines             │  │ ⠋ Common Crawl   Searching…                        │  │
│  ● Bing  ● DDG ...   │  │ Crawling pages… 34/80          [████████░░░░░░░░]  │  │
│                      │  └────────────────────────────────────────────────────┘  │
│  File Types          │                                                           │
│  [✓] Documents  ▶    │  # Filename        Domain    Type  Engine       Actions  │
│  [✓] ECU Tunes  ▶    │  tune.hpt          forum..   HPT   Bing(crawled) Copy   │
│  [ ] Archives   ▶    │  map.bin           site.com  BIN   DDG(crawled)  Copy   │
│                      │  report.pdf        gov.uk    PDF   Archive       Copy   │
│  [⊙] Proxies    ▶    │  [ Filter… ] [Sort ▾] [⬇ CSV] [⬇ URLs] [⬇ JSON]         │
│  [  Search  ]        │  Proxies: 142 active, 3 burned                           │
└──────────────────────┴──────────────────────────────────────────────────────────┘
```

---

## Features

### Two-Phase Search

#### Phase 1 — Multi-Engine Parallel Search
All selected engines run concurrently in separate threads. Results stream back as each engine completes via Server-Sent Events — no waiting for the slowest engine before seeing results.

| Engine | Type | Notes |
|---|---|---|
| **Bing** | Web scraper | Up to 5 pages of results per file type |
| **DuckDuckGo** | Web scraper | Uses HTML lite endpoint; unwraps DDG redirect URLs |
| **Yahoo** | Web scraper | Multi-page; unwraps Yahoo redirect URLs |
| **Startpage** | Web scraper | Google proxy — different IP fingerprint from Google |
| **Mojeek** | Web scraper | Independent crawler index, no Google/Bing affiliation |
| **Common Crawl** | Index API | Free, no rate limits; queries the CC URL Index directly |
| **Internet Archive** | Search API | Searches archive.org's public collection |
| **Google CSE** | Official API | Requires a free Google Custom Search API key + CX ID |
| **SearXNG** | Meta-search API | Point at any public or self-hosted SearXNG instance |

#### Phase 2 — Deep Page Crawling
This is the key feature that makes the tool actually find files. Search engines almost never index direct download URLs — the real files live behind landing pages, forum posts, and download portals.

After Phase 1, the backend visits every result page in parallel (up to 80 pages, 12 threads) and scrapes all `<a href>` links looking for URLs that end in your selected extensions. This catches:

- Files linked from forum threads and download pages
- CDN / mirror links embedded in HTML
- File index pages and FTP-style directories
- Download buttons hidden behind a landing page

Results from crawled pages appear in the table incrementally as pages are visited, tagged with the source engine + `(crawled)`.

**Smart filtering:**
- Only HTML pages are parsed (first 500 KB to keep things fast)
- Non-HTML responses that are themselves a direct file are captured
- Social media, search engines, and other non-file-hosting domains are automatically skipped

### Live Status Panel
A real-time status grid appears the moment you hit Search, showing every active engine and the crawl progress:

| State | Icon | Meaning |
|---|---|---|
| Queued | dim dot | Waiting to start |
| Searching | blue spinner | Engine query in progress |
| Done | green ✓ | `N direct, M pages` |
| Rate limited | red ✗ | Engine blocked the request |
| Error | red ✗ | Connection or parse failure |

A separate crawl progress bar shows pages visited during Phase 2. The panel collapses automatically when everything finishes.

### Rate-Limit Detection & Proxy Rotation
When Bing, DDG, Yahoo, or other scraped engines return a `429`, `403`, CAPTCHA page, or known block phrase:
- The engine row updates to **"Rate limited"** in red
- An orange **toast notification** pops up in the bottom-right corner (auto-dismisses after 5 s)
- Any results collected before the block are still kept
- If proxies are loaded, the blocked proxy is **automatically burned** and the request retries through the next one

#### Built-in Proxy Pool
Open the **Proxies** panel in the sidebar to load a pool of proxies. The tool rotates through them automatically — one click fetches thousands of fresh public proxies:

| Button | Source | Proxies fetched |
|---|---|---|
| **⬇ HTTP** | TheSpeedX/SOCKS-List `http.txt` | ~2,800+ |
| **⬇ SOCKS4** | TheSpeedX/SOCKS-List `socks4.txt` | ~1,000+ |
| **⬇ SOCKS5** | TheSpeedX/SOCKS-List `socks5.txt` | ~2,100+ |

You can also paste your own proxies manually — one per line, any format:
```
1.2.3.4:8080                    # bare host:port → http:// auto-added
http://user:pass@1.2.3.4:8080
socks5://1.2.3.4:1080
socks4://1.2.3.4:1080
```

During a search the live panel shows **Proxies: N active, M burned** in real time. Burned proxies are permanently removed from the pool for that session. SOCKS4/5 requires the `requests[socks]` package (included in `requirements.txt`).

> Free public proxies are unreliable — many will be slow or dead. For serious scraping, supply your own paid proxies.

### Filetype Dork Queries
Every search is constructed as `filetype:<ext> <your query>` — the standard operator recognised by Bing, Yahoo, DuckDuckGo, and Google to bias results toward pages mentioning that file type.

### 60+ File Type Categories

Each category has a **Select All checkbox** in its header — click once to activate or deactivate every type in the group. Individual chips stay in sync.

| Category | Formats |
|---|---|
| Documents | PDF, DOC, DOCX, ODT, RTF, TXT, LaTeX |
| Spreadsheets | XLS, XLSX, CSV, ODS |
| Presentations | PPT, PPTX, ODP, Keynote |
| eBooks | EPUB, MOBI, AZW3, DjVu |
| Archives | ZIP, RAR, 7-Zip, TAR, GZ, BZ2 |
| Audio | MP3, FLAC, WAV, OGG, AAC, M4A |
| Video | MP4, MKV, AVI, MOV, WMV, WebM |
| Data / Code | SQL, JSON, XML, YAML, Python, Shell, JavaScript |
| Images / Other | ISO, APK, EXE, DMG, Torrent |
| **ECU Tunes** | See section below |

#### ECU Tune Formats
Purpose-built category covering all major automotive ECU tuning platforms:

| Extension | Platform |
|---|---|
| `.hpt` / `.hpl` | HP Tuners VCM Suite (tune + data log) |
| `.ctz` / `.e2p` / `.e2s` | EFILive |
| `.tun` | SCT, Bama, Superchips, Diablosport |
| `.bin` / `.hex` | Generic ROM binary / Intel HEX |
| `.xdf` / `.adx` | TunerPro RT (definition + datalog) |
| `.fpk` | Hondata FlashPro |
| `.otf` | COBB Accessport OTS tune |
| `.rdt` | ECUTek |
| `.htf` | Haltech |
| `.pclr` / `.pcl` | Link ECU |
| `.cal` | AEM calibration |
| `.ecm` | ECUMaster |
| `.map` / `.ols` | WinOLS |
| `.a2l` | ASAP2 / INCA (OEM calibration description) |
| `.frf` | OEM Flash Reprogramming File |

---

## Installation

### Requirements
- Python 3.10+
- pip

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/evilgenius79/Websearch.git
cd Websearch

# 2. (Recommended) Create a virtual environment
python -m venv venv
source venv/bin/activate      # Linux / macOS
venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start the server
python app.py
```

Then open **http://localhost:5000** in your browser.

The server binds to `0.0.0.0:5000` by default — accessible from any device on your local network.

---

## Usage

### Basic Search
1. Type your search terms in the **Search Query** box
2. Select **File Types** — use the per-category checkbox or pick individual chips
3. Ensure at least one **Search Engine** chip is highlighted
4. Click **Search** (or press `Enter`)

Phase 1 (engine queries) and Phase 2 (page crawling) run automatically. The live status panel shows real-time progress for both phases. Results appear in the table as they are found.

### Options

| Control | Default | Description |
|---|---|---|
| Max results per engine | 60 | How many results to request from each engine (10–200) |
| Deep crawl | On | Visit result pages to find actual download links |
| Max pages to crawl | 80 | Caps how many pages Phase 2 will visit (10–200) |

Turning off **Deep crawl** skips Phase 2 entirely — useful if you only want to check whether direct-indexed links exist, or if you need faster results.

### Refining Results
- **Filter box** — type any text to instantly filter across filename, URL, domain, engine, type, and snippet
- **Sort** — click any column header (Filename, Domain, Type, Engine) to sort ascending/descending; also available via the dropdown
- **Engine stats pills** — shown after search completes, showing how many links came from each source

### Exporting
| Button | Output |
|---|---|
| **⬇ CSV** | Comma-separated file with URL, filename, domain, type, engine, snippet |
| **⬇ URLs** | Plain text — one URL per line, ready for `wget -i` or a download manager |
| **⬇ JSON** | Full JSON array of all result objects — useful for scripting or further processing |

Export respects the active filter — only visible rows are exported.

### Optional API Credentials
Expand the **Optional API credentials** panel in the sidebar to unlock additional engines:

| Field | Purpose |
|---|---|
| Google CSE API Key | Enables Google CSE. Get a free key at [console.developers.google.com](https://console.developers.google.com) |
| Google CSE CX | Your Custom Search Engine ID (create one at [cse.google.com](https://cse.google.com)) |
| SearXNG Instance URL | URL of any public or self-hosted SearXNG instance (e.g. `https://searx.be`) |

Credentials are never stored — they only exist for the duration of your browser session.

### Proxy Configuration
See the **[Rate-Limit Detection & Proxy Rotation](#rate-limit-detection--proxy-rotation)** section in Features above for full details on proxy formats, auto-fetch buttons, and burn behaviour.

---

## Configuration

All tunable constants are at the top of `app.py`:

| Constant | Default | Description |
|---|---|---|
| `USER_AGENTS` | 7 entries | Rotated randomly on every request to reduce fingerprinting |
| `FILE_EXTENSIONS` | 60+ formats | Add new extensions by inserting into the relevant dict group |
| `_SKIP_DOMAINS` | 15 entries | Domains skipped during page crawl (social media, search engines) |
| `max_results` cap | 200 | Hard server-side ceiling on engine results |
| `max_crawl` cap | 200 | Hard server-side ceiling on pages to crawl |
| Search threads | `len(active engines)` | One thread per active engine |
| Crawl threads | 12 | Fixed pool for Phase 2 page fetching |
| Per-engine timeout | 90 s | `as_completed` timeout for search phase |
| Per-page timeout | 10 s | Individual page fetch timeout during crawl |

### Adding a New File Type

Open `app.py` and add the extension to the appropriate group in `FILE_EXTENSIONS`:

```python
"ECU Tunes": {
    "hpt": "HP Tuners",
    "mynewext": "My Platform",   # ← add here
    ...
},
```

The frontend picks it up automatically — no template changes needed.

### Adding a New Search Engine

1. Write a function returning `(direct_results, pages_to_crawl)`. Accept `proxy_manager` so the engine participates in proxy rotation:
```python
def search_myengine(
    query: str,
    filetypes: list[str],
    max_results: int,
    proxy_manager: "ProxyManager | None" = None,
) -> tuple[list[dict], list[dict]]:
    results, page_hits, seen = [], [], set()
    for ft in filetypes:
        dork = f"filetype:{ft} {query}"
        r = _req("GET", "https://myengine.com/search",
                 proxy_manager=proxy_manager, engine="My Engine",
                 params={"q": dork}, headers=get_headers(), timeout=12)
        # parse r.text with BeautifulSoup ...
        if is_direct_link(url, [ft]):
            results.append(make_result(url, title, snippet, "My Engine", ft))
        else:
            page_hits.append(make_page_hit(url, title, snippet, "My Engine", ft))
    return results, page_hits
```

2. Register it in the `TASKS` dict inside the `/search` route:
```python
TASKS["myengine"] = lambda: search_myengine(query, filetypes, max_results, pm)
```

3. Add a chip to `templates/index.html`:
```html
<div class="engine-chip" data-engine="myengine">
  <span class="dot"></span>My Engine
</div>
```

---

## How It Works

```
Browser  ──POST /search──►  Flask app
                               │
                    ┌──────────┴──────────────────────────────────┐
                    │           Phase 1: Search Engines            │
                    │         ThreadPoolExecutor (N engines)        │
                    │                                               │
              ┌─────┴────┐   ┌────────────┐   ┌───────────────┐   │
              │  Bing     │   │ DuckDuckGo │   │ Common Crawl  │   │
              │  scraper  │   │  scraper   │   │  Index API    │   │
              └─────┬────┘   └─────┬──────┘   └───────┬───────┘   │
                    └──────────────┴──────────────────┘            │
                               │                                   │
                  ┌────────────┴──────────────┐                    │
                  │  direct links             │  page URLs         │
                  │  → stream to browser      │  → crawl queue     │
                  └───────────────────────────┘                    │
                                                                   │
                    ┌──────────┴──────────────────────────────────┐
                    │           Phase 2: Page Crawler              │
                    │         ThreadPoolExecutor (12 workers)       │
                    │                                               │
              ┌─────┴──────┐  ┌──────────────┐  ┌──────────────┐  │
              │ forum page │  │ download page│  │ index page   │  │
              │ → scrape   │  │  → scrape    │  │  → scrape    │  │
              └─────┬──────┘  └──────┬───────┘  └──────┬───────┘  │
                    └────────────────┴─────────────────┘            │
                               │                                   │
                  direct file links extracted from <a href>        │
                  → deduplicate → stream to browser                │
                    └──────────────────────────────────────────────┘
                               │
                    ◄── SSE stream (engine_done, crawl_progress, done) ──
```

### SSE Event Flow

The `/search` route streams [Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events) so the browser receives results in real time without polling:

| Event | When fired | Key payload fields |
|---|---|---|
| `engines_registered` | Before search starts | `engines` — list of active engine names |
| `engine_start` | Thread submitted | `engine` |
| `engine_done` | Engine thread finished | `results`, `count`, `pages`, `total`, `proxies_active`, `proxies_burned` |
| `engine_error` | Engine threw exception | `error`, `rate_limited`, `proxies_active`, `proxies_burned` |
| `crawl_start` | Phase 2 begins | `page_count` |
| `crawl_progress` | Every 3 pages crawled | `results`, `crawled`, `of`, `total` |
| `crawl_done` | All pages visited | `crawled`, `total` |
| `done` | Everything complete | `total` |

---

## Dependencies

| Package | Purpose |
|---|---|
| `flask` | Web framework, template rendering, streaming responses |
| `requests` | HTTP client for engine queries and page crawling |
| `requests[socks]` | Adds SOCKS4/5 proxy support via PySocks |
| `beautifulsoup4` | HTML parsing for scraped engines and crawled pages |
| `lxml` | Fast HTML/XML parser backend for BeautifulSoup |

---

## Notes & Limitations

- **Rate limiting** — Search engines may temporarily block repeated queries. Random delays (`jitter()`) are built in. Use the Proxies panel to auto-fetch and rotate through public proxies when engines block you. Reduce `max_results` or disable engines if blocks persist.
- **Scraper fragility** — HTML-scraped engines (Bing, DDG, Yahoo, etc.) may break if those sites change their page markup. API-based engines (Common Crawl, Internet Archive, Google CSE) are more stable.
- **Crawl depth** — Phase 2 only follows links on the direct result pages. It does not recursively crawl (no second-level pages). If a file is two clicks deep from a search result it won't be found.
- **JavaScript-rendered pages** — The crawler uses plain `requests` + BeautifulSoup and does not execute JavaScript. Pages that load their download links via JS won't be crawled successfully.
- **Google CSE scope** — Google's free Custom Search API returns a maximum of 100 results (10 pages × 10). Set your CSE to "Search the entire web" for broadest coverage.
- **Common Crawl freshness** — The CC index lags behind the live web by weeks to months. Good for finding historically published files; not for fresh content.
- **Internet Archive** — Only returns items explicitly archived or uploaded to archive.org; not a general web index.

---

## License

MIT — do whatever you want, no warranty provided.
