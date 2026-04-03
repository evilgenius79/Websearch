# Multi-Engine File Search

> Aggressive parallel file hunting across multiple search engines — direct download links only.

A self-hosted web application that fires filetype dork queries at multiple search engines simultaneously and surfaces only direct download links. Built with a Python/Flask backend and a modern dark-themed frontend.

---

## Screenshot

```
┌─────────────────────────────────────────────────────────────────────────┐
│  🔍 Multi-Engine File Search                                            │
│  Aggressive parallel search across multiple engines · direct links only │
├──────────────────┬──────────────────────────────────────────────────────┤
│  Search Query    │  # Filename      Domain    Type   Engine   Actions   │
│  ─────────────   │  ──────────────────────────────────────────────────  │
│  Engines         │  report.pdf      gov.uk    PDF    Bing     Copy URL  │
│  □ Bing          │  dataset.csv     data.io   CSV    DDG      Copy URL  │
│  □ DuckDuckGo    │  manual.epub     archive   EPUB   Archive  Copy URL  │
│  □ Yahoo  ...    │  tune.hpt        forums..  HPT    Yahoo    Copy URL  │
│                  │                                                       │
│  File Types      │  [ Filter results… ]  [Sort ▾]  [⬇ CSV] [⬇ URLs]   │
│  > Documents     │                                                       │
│  > ECU Tunes     │                                                       │
│  > Archives ...  │                                                       │
│                  │                                                       │
│  [  Search  ]    │                                                       │
└──────────────────┴──────────────────────────────────────────────────────┘
```

---

## Features

### Multi-Engine Parallel Search
All selected engines run concurrently in separate threads. Results stream back as each engine completes — no waiting for the slowest engine before seeing results.

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

### Filetype Dork Queries
Every search is constructed as `filetype:<ext> <your query>` — the standard operator recognised by Google, Bing, Yahoo, and DuckDuckGo to restrict results to a specific file extension. Direct-link filtering then verifies the URL path actually ends in the extension before including it in results.

### Direct-Link Filtering
Results that don't end in the requested file extension are silently discarded. Every link in the results table is a URL you can paste directly into a browser or download manager and get the file.

### 60+ File Type Categories

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
2. Select one or more **File Types** from the accordion categories
3. Ensure at least one **Search Engine** chip is highlighted
4. Click **Search** (or press `Enter`)

Results appear in the table as the backend finishes querying each engine. A progress bar and live status indicator show what's happening.

### Refining Results
- **Filter box** — type any text to instantly filter results across filename, URL, domain, engine, type, and snippet fields
- **Sort dropdown / column headers** — click any column header (Filename, Domain, Type, Engine) to sort ascending/descending; also available via the dropdown
- **Max results slider** — control how deep each engine searches (10–200 per engine)

### Exporting
| Button | Output |
|---|---|
| **⬇ CSV** | Comma-separated file with URL, filename, domain, type, engine, snippet |
| **⬇ URLs** | Plain text file — one URL per line, ready for `wget -i` or a download manager |

### Optional API Credentials
Expand the **Optional API credentials** panel in the sidebar to unlock additional engines:

| Field | Purpose |
|---|---|
| Google CSE API Key | Enables the Google Custom Search engine. Get a free key at [console.developers.google.com](https://console.developers.google.com) |
| Google CSE CX | Your Custom Search Engine ID (create one at [cse.google.com](https://cse.google.com)) |
| SearXNG Instance URL | URL of any public or self-hosted SearXNG instance (e.g. `https://searx.be`) |

Credentials are never stored — they only exist in your browser session.

---

## Configuration

All tunable constants are at the top of `app.py`:

| Constant | Default | Description |
|---|---|---|
| `USER_AGENTS` | 7 entries | Rotated randomly on every request to reduce fingerprinting |
| `FILE_EXTENSIONS` | 60+ formats | Add new extensions by inserting into the relevant dict group |
| `max_results` cap | 200 | Hard ceiling enforced server-side regardless of client input |
| Worker threads | `len(active engines)` | One thread per active engine; scales automatically |
| Per-engine timeout | 60 s total / 30 s per future | Controlled via `ThreadPoolExecutor` and `as_completed` |

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

1. Write a function following the existing pattern:
```python
def search_myengine(query: str, filetypes: list[str], max_results: int) -> list[dict]:
    results, seen = [], set()
    # ... scrape / call API ...
    results.append(make_result(url, title, snippet, "My Engine", ft))
    return results
```

2. Register it in the `TASKS` dict inside the `/search` route:
```python
if "myengine" in engines:
    TASKS["myengine"] = lambda: search_myengine(query, filetypes, max_results)
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
                    ┌──────────┴──────────┐
                    │  ThreadPoolExecutor  │
                    │                      │
              ┌─────┴────┐          ┌──────┴────┐
              │  Bing     │  ...     │  Archive  │
              │  scraper  │          │  API      │
              └─────┬────┘          └──────┬────┘
                    │                      │
                    └──────────┬───────────┘
                               │
                      deduplicate by URL
                      filter: is_direct_link()
                               │
                    ◄──JSON response──────────
```

Each engine function:
1. Builds a `filetype:<ext> <query>` dork for every selected extension
2. Makes HTTP requests with a randomly-chosen user agent
3. Parses the response HTML (or JSON for APIs)
4. Unwraps any redirect URLs (DDG `uddg=`, Yahoo `/RU=/`, etc.)
5. Passes each candidate URL through `is_direct_link()` — checks the URL path ends with the extension
6. Returns a list of result dicts

The main route merges all results, deduplicates on URL, and returns JSON to the browser.

---

## Dependencies

| Package | Purpose |
|---|---|
| `flask` | Web framework and template rendering |
| `requests` | HTTP client for all engine queries |
| `beautifulsoup4` | HTML parsing for scraped engines |
| `lxml` | Fast HTML/XML parser backend for BeautifulSoup |

---

## Notes & Limitations

- **Rate limiting** — Search engines may temporarily block repeated queries. Adding delays (`jitter()`) is already built in; reduce `max_results` or disable engines if you hit blocks.
- **Scraper fragility** — HTML-scraped engines (Bing, DDG, Yahoo, etc.) may break if those sites change their markup. The API-based engines (Common Crawl, Internet Archive, Google CSE) are more stable.
- **Google CSE scope** — Google's free Custom Search API returns a maximum of 100 results (10 pages × 10). Results depend on how you configure your CSE — set it to "Search the entire web" for broadest coverage.
- **Common Crawl freshness** — The CC index lags behind live web by weeks to months. Good for finding historically published files; not for fresh content.
- **Internet Archive** — Only returns items that have been explicitly archived/uploaded; not a general web index.

---

## License

MIT — do whatever you want, no warranty provided.
