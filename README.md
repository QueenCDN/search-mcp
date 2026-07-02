# search-mcp

A lightweight, production-grade [MCP](https://modelcontextprotocol.io) server that gives
[AnythingLLM](https://anythingllm.com) (or any MCP client) real internet access via DuckDuckGo
search + full-page content extraction, without relying on AnythingLLM's built-in web browsing
pipeline.

Built for a resource-constrained host (Intel iMac, 8 GB RAM): no API keys, no paid services,
a small dependency footprint, bounded concurrency, and hard caps on how much text is ever sent
back to the model.

```
User -> AnythingLLM -> Gemma 3 4B (Ollama) -> search-mcp (this project) -> DuckDuckGo + live web pages
```

## What it does

Exposes exactly three MCP tools:

| Tool          | Purpose                                                              |
|---------------|-----------------------------------------------------------------------|
| `search_web`  | General web search: DuckDuckGo query -> top results -> download pages -> cleaned text |
| `search_news` | News-focused search, capped at 5 results, falls back to the article snippet if a page can't be scraped |
| `fetch_page`  | Read one specific URL and return only its cleaned, readable content |

Every tool:

- Downloads pages concurrently (small thread pool) with an 8 second per-page timeout.
- Extracts readable content with `trafilatura` first, falling back to a `BeautifulSoup4`
  strip-and-scrape pass if trafilatura can't get a clean result.
- Strips navigation, headers, footers, ads, cookie banners, and menu boilerplate.
- Skips any page that times out, 404s, gets blocked (403/429/Cloudflare-style challenges), or
  isn't HTML — without ever failing the whole request because of one bad page.
- Caps output at 4,000 characters per page and 20,000 characters combined, so Gemma 3 4B never
  receives an oversized context.
- Caches identical queries/URLs in memory for 5 minutes.
- Logs every stage (search start/finish, pages downloaded/skipped, cache hits, errors) to stderr.

## Project layout

```
search-mcp/
├── server.py                    # FastMCP server: defines the 3 MCP tools
├── search.py                    # DuckDuckGo querying, concurrent scraping, caching, formatting
├── scraper.py                   # HTTP fetch + trafilatura/BeautifulSoup extraction
├── utils.py                     # Config loading, logging, TTL cache, text cleanup
├── requirements.txt
├── .env                         # Tunable limits (see below)
├── anythingllm_mcp_servers.json # Example AnythingLLM MCP registration
└── README.md
```

## Requirements

- Python 3.11+ (tested with 3.11–3.14)
- macOS Monterey 12 (or any OS — nothing here is macOS-specific)
- No API keys, no paid accounts

## Installation

```bash
cd ~/search-mcp

# create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# install dependencies
pip install -r requirements.txt
```

To deactivate the virtual environment later: `deactivate`.

## Configuration

All limits live in `.env` (loaded automatically via `python-dotenv`) and can also be set as
regular environment variables — environment variables take precedence if both are set.

| Variable               | Default | Meaning |
|------------------------|---------|---------|
| `SEARCH_MAX_RESULTS`   | 5       | Max DuckDuckGo results fetched for `search_web` |
| `SEARCH_MAX_PAGES`     | 5       | Max pages actually downloaded per web search |
| `NEWS_MAX_RESULTS`     | 5       | Max results for `search_news` |
| `PAGE_TIMEOUT`         | 8       | Per-page HTTP timeout, in seconds |
| `REQUEST_MAX_WORKERS`  | 4       | Thread pool size for concurrent page downloads |
| `SEARCH_RETRY_ATTEMPTS`| 3       | Retries for a DuckDuckGo query if it transiently returns zero results |
| `MAX_CHARS_PER_PAGE`   | 4000    | Character cap applied to each individual page |
| `MAX_TOTAL_CHARS`      | 20000   | Character cap applied to the combined tool output |
| `CACHE_TTL_SECONDS`    | 300     | In-memory cache lifetime for identical queries/URLs |
| `USER_AGENT`           | (desktop Chrome/macOS UA) | Sent on every outbound HTTP request |
| `LOG_LEVEL`            | INFO    | Python logging level (`DEBUG`, `INFO`, `WARNING`, ...) |

Edit `.env` directly, or override per-deployment via the `env` block in
`anythingllm_mcp_servers.json`.

## Running the server standalone

The server communicates over stdio, which is how AnythingLLM (and every other MCP client) talks
to it — you don't "browse to" it like a web server.

```bash
source .venv/bin/activate
python server.py
```

It will sit waiting for MCP protocol messages on stdin/stdout and log activity to stderr. Use
`Ctrl+C` to stop it. You generally won't run it this way in production; AnythingLLM launches it
as a subprocess automatically (see below).

For interactive debugging, FastMCP ships an inspector:

```bash
fastmcp dev server.py
```

This opens a local web UI where you can call `search_web`, `search_news`, and `fetch_page`
directly and inspect raw results.

## Connecting to AnythingLLM

1. Locate AnythingLLM's MCP configuration. In the desktop app this is exposed under
   **Settings -> Agent Skills / Tools -> MCP Servers** (AnythingLLM writes this to a
   `plugins/anythingllm_mcp_servers.json` file inside its storage directory). If you're editing
   the file directly, merge in the contents of the example file in this repo:
   [`anythingllm_mcp_servers.json`](anythingllm_mcp_servers.json).

2. Update the `command` and `args` paths to point at **your** virtual environment's Python
   interpreter and **your** absolute path to `server.py`, for example:

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

   Always use the venv's interpreter (`.venv/bin/python`), not a bare `python3` — otherwise the
   subprocess AnythingLLM launches won't have `fastmcp`, `trafilatura`, etc. installed.

3. Restart AnythingLLM (or reload MCP servers from its settings UI). The three tools
   (`search_web`, `search_news`, `fetch_page`) should appear as available tools for the workspace
   / agent.

4. In your workspace's agent configuration, enable the `search-mcp` tools and disable
   AnythingLLM's built-in web search/browsing skill so Gemma calls this server instead.

5. Ask Gemma something that requires current information, e.g. *"What's the latest version of
   Ollama and what changed?"* — it should invoke `search_web` (visible in AnythingLLM's agent
   trace) and answer from the returned page content.

## Testing examples

### Quick sanity check without AnythingLLM

With the virtual environment active:

```bash
python -c "from search import search_web; print(search_web('capital of France'))"
python -c "from search import search_news; print(search_news('local AI models'))"
python -c "from search import fetch_single_page; print(fetch_single_page('https://en.wikipedia.org/wiki/Ollama'))"
```

### Exercising the real MCP protocol path

This calls the tools exactly the way AnythingLLM would, over FastMCP's client, without needing a
full AnythingLLM install:

```bash
python - <<'PY'
import asyncio
from fastmcp import Client
import server

async def main():
    async with Client(server.mcp) as client:
        result = await client.call_tool("search_web", {"query": "latest Ollama release notes"})
        print(result.content[0].text)

asyncio.run(main())
PY
```

### Checking logs

Run the server directly and watch stderr for structured log lines:

```bash
python server.py
```

```
2026-07-02 04:18:07 | INFO | search_mcp.search  | Search started: web query='latest Ollama release'
2026-07-02 04:18:09 | WARNING | search_mcp.scraper | Fetch blocked for https://example.com/... (HTTP 403) - likely bot protection, skipping
2026-07-02 04:18:09 | INFO | search_mcp.search  | Pages downloaded: 4, pages skipped: 1
2026-07-02 04:18:09 | INFO | search_mcp.search  | Search finished: web query='latest Ollama release' in 2.31s
```

## Performance notes (why this stays lightweight on an 8 GB iMac)

- Page downloads use a small `ThreadPoolExecutor` (default 4 workers) — cheap for I/O-bound HTTP
  requests, without spinning up async infrastructure or extra processes.
- Nothing is held in memory beyond the in-process TTL cache, which only stores already-truncated,
  cleaned text (never raw HTML).
- `trafilatura` and `lxml` are the only "heavy" dependencies; both are pure/compiled libraries
  with modest memory footprints — there is no headless browser, no Selenium, no Playwright.
- Hard character caps (`MAX_CHARS_PER_PAGE`, `MAX_TOTAL_CHARS`) bound both memory use and the size
  of the context handed to Gemma 3 4B, which matters for both RAM and inference latency on this
  hardware.

## Known limitations

- DuckDuckGo's underlying scraping backends (used internally by the `duckduckgo-search` package)
  are occasionally rate-limited and can return zero results for a well-formed query; `search_web`
  and `search_news` retry up to `SEARCH_RETRY_ATTEMPTS` times before giving up and returning a
  "no results" message rather than an error.
- Some sites (Cloudflare-protected pages, paywalled news sites) will always be skipped — this is
  by design (`Never stop because of one failed request`), not a bug. `search_news` falls back to
  the DuckDuckGo snippet text for any article it can't scrape.
- The `duckduckgo-search` package on PyPI has announced a rename to `ddgs`; if a future version
  removes the `duckduckgo_search` import path, update the `import` in `search.py` and the entry
  in `requirements.txt` accordingly — no other code changes are needed.
