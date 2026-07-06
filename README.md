# search-mcp

A production-grade [MCP](https://modelcontextprotocol.io) server that gives
[AnythingLLM](https://anythingllm.com) (or any MCP client) real internet access — web search,
news, page reading, weather, currency rates, Wikipedia — plus offline utilities (time, unit
conversion, calculator, random data). No API keys, no paid services.

Built for a resource-constrained host (Intel iMac, 8 GB RAM): small dependency footprint,
bounded concurrency, hard caps on memory and on how much text is ever sent back to the model.

```
User -> AnythingLLM -> Gemma 3 4B (Ollama) -> search-mcp (this project) -> internet
```

## Why not the built-in search / duckduckgo-search?

The `duckduckgo-search` PyPI package hardcodes scraping backends that get blocked, which is
where the persistent `403 Ratelimit` errors come from. This project talks to the search
backends directly and defensively:

- **Cascading providers** — web: DuckDuckGo HTML → DuckDuckGo Lite → Bing Web RSS;
  news: Bing News RSS → Google News RSS → web cascade. One blocked backend never
  breaks a search.
- **Per-provider rate limiting** — a polite minimum interval between requests to the same
  backend (default 2s), with jitter.
- **Circuit breaker** — a backend that returns 403/429 is taken out of rotation with an
  exponentially growing cooldown (up to 15 min) instead of being hammered while blocked.
- **Retries with exponential backoff** on empty cascades, plus request-level retries for
  transient 5xx from the free APIs.
- **Smart caching** (LRU + TTL + memory cap) so repeated questions don't hit the network
  at all, and identical concurrent queries are computed once.

## The 12 tools

| Tool | What it does |
|---|---|
| `search_web(query)` | Cascading web search; downloads the top pages, strips boilerplate, returns readable text. Language/region are auto-detected from the query; authoritative sources (Reuters, AP, BBC, TechCrunch, Wikipedia, docs…) rank first; junk/social domains are filtered out. |
| `search_news(query)` | News-index search (Bing News / Google News), max 5 results, with dates and source names; falls back to snippets for paywalled sites. |
| `fetch_page(url)` | Reads one URL and returns only the cleaned article text. |
| `current_time(location="")` | Time, date, weekday (EN+RU), timezone, UTC offset for a city/country ("Moscow", "Стамбул") or IANA zone. |
| `current_date(location="")` | Date, weekday, month, ISO week number, day of year. |
| `weather(location)` | Current conditions + 3-day forecast via free Open-Meteo: temperature, feels-like, humidity, wind, precipitation probability. |
| `currency_rate(from, to, amount=1)` | Exchange rates via open.er-api.com with frankfurter.app (ECB) fallback. Accepts ISO codes, symbols, and names: `USD`, `$`, `доллар`, `₺`. Supports RUB, TRY, and ~160 others. |
| `wikipedia_search(query, language="")` | Article summary + link; the Wikipedia language edition follows the query language automatically. |
| `unit_converter(value, from_unit, to_unit)` | Mass, length, temperature, volume, area, speed, data, time. English and Russian unit names (`kg`/`кг`, `мили`, `галлоны`). |
| `calculator(expression)` | Safe math: arithmetic, `%` percentages, `^` powers, roots, trigonometry, logarithms, factorials, constants `pi`/`e`. AST-sandboxed — no code execution possible. |
| `generate_uuid(count=1)` | 1–50 random UUIDv4 values. |
| `random_generator(kind, ...)` | Random integers/floats in a range, strings, secure passwords, coin flips, dice (`2d6`), or a choice from a list. |

## Project layout

```
search-mcp/
├── server.py            # FastMCP server: registers the 12 tools, stats, error guard
├── search.py            # Search orchestration: cascade -> scrape -> format, caching
├── providers.py         # Search backends, ranking, trusted/blocked domain lists
├── scraper.py           # Page download + trafilatura/BS4 extraction
├── net.py               # Pooled HTTP session, rate limiter, circuit breaker
├── cache.py             # LRU+TTL cache with memory bound and in-flight dedup
├── local_tools.py       # time/date/converter/calculator/uuid/random (offline)
├── external_tools.py    # weather/currency/wikipedia (free APIs)
├── utils.py             # config, colored logging, stats, language detection
├── tests/               # pytest suite (152 offline tests + 9 live tests)
├── requirements.txt     # runtime dependencies
├── requirements-dev.txt # + pytest
├── .env                 # tunable limits (see below)
└── anythingllm_mcp_servers.json  # example AnythingLLM registration
```

## Requirements

- Python 3.11+ (3.12 recommended; tested on 3.12–3.14)
- macOS / Linux / Windows — nothing platform-specific
- No API keys, no accounts

## Installation

```bash
cd ~/search-mcp

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## Configuration

Everything lives in `.env` (auto-loaded) and can be overridden by real environment variables,
including the `env` block of the AnythingLLM config.

| Variable | Default | Meaning |
|---|---|---|
| `SEARCH_MAX_RESULTS` | 5 | Max results per web search |
| `SEARCH_MAX_PAGES` | 5 | Max pages downloaded per search |
| `NEWS_MAX_RESULTS` | 5 | Max news results |
| `SEARCH_RETRY_ATTEMPTS` | 2 | Full-cascade retry passes (exponential backoff) |
| `SEARCH_REGION` | auto | `auto` = derive from query language; or pin `ru-ru`, `us-en`, `de-de`… |
| `PAGE_TIMEOUT` | 8 | Per-page HTTP timeout, seconds |
| `REQUEST_MAX_WORKERS` | 4 | Thread pool size for page downloads |
| `MAX_HTML_BYTES` | 2000000 | Download size cap per page (memory protection) |
| `PROVIDER_MIN_INTERVAL` | 2.0 | Min seconds between hits to the same search backend |
| `PROVIDER_COOLDOWN` | 120 | Base circuit-breaker cooldown after a block, doubles per failure |
| `MAX_CHARS_PER_PAGE` | 4000 | Text cap per page sent to the model |
| `MAX_TOTAL_CHARS` | 20000 | Combined output cap per tool call |
| `CACHE_TTL_SECONDS` | 300 | Search result cache lifetime |
| `CACHE_MAX_ENTRIES` | 256 | Cache entry cap (LRU eviction) |
| `CACHE_MAX_MEMORY_MB` | 32 | Cache memory cap (LRU eviction) |
| `PAGE_TTL_SECONDS` | 900 | fetch_page cache lifetime |
| `WEATHER_TTL_SECONDS` | 600 | Weather cache lifetime |
| `CURRENCY_TTL_SECONDS` | 3600 | Currency rate cache lifetime |
| `WIKI_TTL_SECONDS` | 86400 | Wikipedia summary cache lifetime |
| `DEFAULT_TIMEZONE` | (local) | Timezone used when `current_time()` gets no location |
| `LOG_LEVEL` | INFO | DEBUG / INFO / WARNING / ERROR |
| `STATS_LOG_EVERY` | 20 | Log a stats summary every N tool calls (0 = off) |

## Running the server

The server speaks MCP over stdio — AnythingLLM launches it as a subprocess; you don't
normally run it by hand:

```bash
source .venv/bin/activate
python server.py        # waits for MCP messages on stdin, logs to stderr
```

For interactive debugging, FastMCP ships an inspector UI:

```bash
fastmcp dev server.py
```

## Connecting to AnythingLLM

1. Open AnythingLLM → **Settings → Agent Skills → MCP Servers** (the config file is
   `plugins/anythingllm_mcp_servers.json` inside AnythingLLM's storage directory).
   Merge in [`anythingllm_mcp_servers.json`](anythingllm_mcp_servers.json) from this repo.

2. Fix the two paths for your machine — they must point at the **venv's** Python and the
   absolute path of `server.py`:

   ```json
   {
     "mcpServers": {
       "search-mcp": {
         "command": "/Users/yourname/search-mcp/.venv/bin/python",
         "args": ["/Users/yourname/search-mcp/server.py"],
         "type": "stdio"
       }
     }
   }
   ```

3. Restart AnythingLLM (or reload MCP servers in its UI). All 12 tools should appear.

4. In the workspace's agent settings, enable the `search-mcp` tools and disable the built-in
   web browsing skill so the model uses this server instead.

5. Ask something that needs the internet — e.g. *"Какая сейчас погода в Москве?"* or
   *"OpenAI latest news"* — and watch the agent call the tools in the trace.

## Testing

```bash
pip install -r requirements-dev.txt

pytest              # 152 offline tests: cache, ranking, parsing, tools, MCP protocol
pytest -m live -v   # 9 live tests against real services (network required)
```

Quick manual checks without AnythingLLM:

```bash
python -c "from search import search_web; print(search_web('OpenAI latest news'))"
python -c "from external_tools import weather; print(weather('Москва'))"
python -c "from external_tools import currency_rate; print(currency_rate('USD','RUB',100))"
python -c "from local_tools import calculator; print(calculator('2^10 + 15% * 200'))"
```

Or through the real MCP protocol:

```bash
python - <<'PY'
import asyncio
from fastmcp import Client
import server

async def main():
    async with Client(server.mcp) as client:
        r = await client.call_tool("search_web", {"query": "latest Ollama release"})
        print(r.content[0].text)

asyncio.run(main())
PY
```

## Reliability & performance notes

- **No crash policy**: every tool is wrapped in a guard that converts any exception into a
  readable message; scraping failures degrade to search snippets; missing providers degrade
  to the next one in the cascade. The server process itself never dies from a bad page.
- **Memory**: pages are streamed and cut at `MAX_HTML_BYTES`; the cache is capped by entries
  *and* bytes; one long-lived thread pool (4 workers) handles downloads; no headless browser.
- **Connections**: a single pooled `requests.Session` reuses TCP/TLS connections across all
  requests.
- **Observability**: colored structured logs (timings per tool call, pages downloaded/skipped,
  cache hits, provider cooldowns) plus a periodic stats summary with process memory.

## Known limitations

- Some sites (hard paywalls, Cloudflare-protected pages) can never be scraped; they are
  detected and replaced with their search snippet rather than failing the request.
- Google News article links are opaque redirects; those results contribute headline, source,
  and date (not full text) — the Bing News provider, which gives direct URLs, runs first.
- Free rate APIs update roughly daily; don't use `currency_rate` for trading decisions.
