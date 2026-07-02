"""MCP server entry point exposing web search tools for AnythingLLM.

Exposes exactly three tools over the Model Context Protocol using FastMCP:

* ``search_web``   - general web search with page content extraction.
* ``search_news``  - news-focused search with page content extraction.
* ``fetch_page``   - fetch and clean a single URL.

Each tool delegates to ``search.py`` for orchestration, which in turn uses
``scraper.py`` for downloading/extraction and ``utils.py`` for shared
config, logging, caching, and text cleanup. Tool wrappers here catch all
exceptions so a single unexpected failure never takes down the MCP server
process AnythingLLM is talking to.
"""

from __future__ import annotations

from fastmcp import FastMCP

from search import fetch_single_page, search_news as run_news_search, search_web as run_web_search
from utils import get_logger, setup_logging

setup_logging()
logger = get_logger("server")

mcp = FastMCP(
    name="search-mcp",
    instructions=(
        "Provides live internet access via DuckDuckGo search and web page "
        "reading. Use search_web for general queries, search_news for "
        "current-events queries, and fetch_page to read one known URL in "
        "full. All tools return cleaned, readable plain text."
    ),
)


@mcp.tool
def search_web(query: str) -> str:
    """Search the web for a query and return cleaned content from top results.

    Runs a DuckDuckGo search, downloads up to 5 of the top results, strips
    navigation/ads/boilerplate from each page, and returns the combined
    readable text. Use this for general knowledge, how-to, or factual
    questions that require current information from the internet.

    Args:
        query: The search query in natural language.

    Returns:
        Cleaned, readable text combined from the top search results, or a
        short message explaining why no content was available.
    """
    try:
        return run_web_search(query)
    except Exception as exc:  # noqa: BLE001 - tool boundary must never raise
        logger.exception("search_web crashed for query=%r", query)
        return f"search_web encountered an unexpected error: {exc}"


@mcp.tool
def search_news(query: str) -> str:
    """Search for recent news articles and return cleaned article content.

    Runs a DuckDuckGo news search limited to 5 results, downloads each
    article, strips boilerplate, and returns the combined readable text.
    Use this specifically for current-events or "latest news about X"
    style questions.

    Args:
        query: The news search query in natural language.

    Returns:
        Cleaned, readable text combined from the top news results, or a
        short message explaining why no content was available.
    """
    try:
        return run_news_search(query)
    except Exception as exc:  # noqa: BLE001 - tool boundary must never raise
        logger.exception("search_news crashed for query=%r", query)
        return f"search_news encountered an unexpected error: {exc}"


@mcp.tool
def fetch_page(url: str) -> str:
    """Fetch a single webpage and return its cleaned readable content.

    Use this when you already have a specific URL (from a prior search
    result, or given directly by the user) and need its full readable
    content rather than a search snippet.

    Args:
        url: The full http(s) URL of the page to read.

    Returns:
        Cleaned, readable text extracted from the page, or a short message
        explaining why the page could not be read.
    """
    try:
        return fetch_single_page(url)
    except Exception as exc:  # noqa: BLE001 - tool boundary must never raise
        logger.exception("fetch_page crashed for url=%r", url)
        return f"fetch_page encountered an unexpected error: {exc}"


if __name__ == "__main__":
    logger.info("Starting search-mcp server (stdio transport)")
    mcp.run()
