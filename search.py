"""Search orchestration: DuckDuckGo lookups + concurrent page scraping.

This module ties ``scraper.py`` (page download + extraction) to the
DuckDuckGo search API to implement the three behaviors exposed as MCP
tools: general web search, news search, and single-page fetch. It also
owns result formatting and the query-level cache.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

from duckduckgo_search import DDGS

from scraper import fetch_and_extract
from utils import (
    MAX_CHARS_PER_PAGE,
    MAX_TOTAL_CHARS,
    NEWS_MAX_RESULTS,
    REQUEST_MAX_WORKERS,
    SEARCH_MAX_PAGES,
    SEARCH_MAX_RESULTS,
    SEARCH_RETRY_ATTEMPTS,
    cache,
    get_logger,
    truncate_text,
)

logger = get_logger("search")

# DuckDuckGo's scraping backends (html/lite/bing, selected internally by the
# duckduckgo-search library) are occasionally rate-limited and return an
# empty result set with no exception raised. A short retry with backoff
# recovers from this transient case without meaningfully slowing down the
# common (first-try-succeeds) path.
_RETRY_BACKOFF_SECONDS = 0.75


@dataclass
class SearchHit:
    """A single DuckDuckGo result before its page has been downloaded."""

    url: str
    title: str
    snippet: str = ""
    source: str = ""
    date: str = ""


@dataclass
class PageResult:
    """A successfully downloaded and cleaned page."""

    url: str
    title: str
    content: str


def _with_retries(fetch_once, description: str) -> list[SearchHit]:
    """Call ``fetch_once`` up to ``SEARCH_RETRY_ATTEMPTS`` times.

    The DuckDuckGo search backends occasionally return an empty result set
    when transiently rate-limited, without raising an exception. Since a
    real zero-result query is rare, a short bounded retry meaningfully
    improves reliability without punishing the common case where the first
    attempt already succeeds.
    """
    last_error: Optional[Exception] = None
    for attempt in range(1, SEARCH_RETRY_ATTEMPTS + 1):
        try:
            hits = fetch_once()
        except Exception as exc:  # noqa: BLE001 - retried below, re-raised if exhausted
            last_error = exc
            logger.warning(
                "%s attempt %d/%d raised %s", description, attempt, SEARCH_RETRY_ATTEMPTS, exc
            )
        else:
            if hits:
                return hits
            logger.info(
                "%s attempt %d/%d returned no results, %s",
                description,
                attempt,
                SEARCH_RETRY_ATTEMPTS,
                "retrying" if attempt < SEARCH_RETRY_ATTEMPTS else "giving up",
            )
        if attempt < SEARCH_RETRY_ATTEMPTS:
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)

    if last_error is not None:
        raise last_error
    return []


def _ddgs_text_search(query: str, max_results: int) -> list[SearchHit]:
    def fetch_once() -> list[SearchHit]:
        hits: list[SearchHit] = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                url = item.get("href") or item.get("url") or ""
                if not url:
                    continue
                hits.append(
                    SearchHit(
                        url=url,
                        title=item.get("title", "").strip(),
                        snippet=item.get("body", "").strip(),
                    )
                )
        return hits

    return _with_retries(fetch_once, f"DuckDuckGo text search for '{query}'")


def _ddgs_news_search(query: str, max_results: int) -> list[SearchHit]:
    def fetch_once() -> list[SearchHit]:
        hits: list[SearchHit] = []
        with DDGS() as ddgs:
            for item in ddgs.news(query, max_results=max_results):
                url = item.get("url") or item.get("href") or ""
                if not url:
                    continue
                hits.append(
                    SearchHit(
                        url=url,
                        title=item.get("title", "").strip(),
                        snippet=item.get("body", "").strip(),
                        source=item.get("source", "").strip(),
                        date=item.get("date", "").strip(),
                    )
                )
        return hits

    return _with_retries(fetch_once, f"DuckDuckGo news search for '{query}'")


def _download_pages(hits: list[SearchHit], max_pages: int) -> list[PageResult]:
    """Download and extract content for up to ``max_pages`` hits concurrently.

    Uses a small thread pool since this workload is I/O-bound (network
    waits dominate); this keeps memory overhead low while still avoiding
    a fully serial 5x8s worst case on slower hardware.
    """
    targets = hits[:max_pages]
    results: list[PageResult] = []
    downloaded = 0
    skipped = 0

    max_workers = max(1, min(REQUEST_MAX_WORKERS, len(targets) or 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_hit = {
            executor.submit(fetch_and_extract, hit.url): hit for hit in targets
        }
        for future in as_completed(future_to_hit):
            hit = future_to_hit[future]
            try:
                content = future.result()
            except Exception as exc:  # noqa: BLE001 - one bad page must not abort the batch
                logger.warning("Unhandled error extracting %s: %s", hit.url, exc)
                skipped += 1
                continue

            if content:
                results.append(
                    PageResult(
                        url=hit.url,
                        title=hit.title or hit.url,
                        content=truncate_text(content, MAX_CHARS_PER_PAGE),
                    )
                )
                downloaded += 1
            else:
                logger.info("Skipped page (no usable content): %s", hit.url)
                skipped += 1

    logger.info("Pages downloaded: %d, pages skipped: %d", downloaded, skipped)

    # Preserve original ranking order rather than completion order.
    order = {hit.url: i for i, hit in enumerate(targets)}
    results.sort(key=lambda r: order.get(r.url, 0))
    return results


def _format_pages(pages: list[PageResult], hits: list[SearchHit]) -> str:
    """Combine per-page content into one bounded, model-friendly string."""
    if not pages:
        return "No readable content could be extracted from the search results."

    snippet_by_url = {hit.url: hit.snippet for hit in hits}
    sections: list[str] = []
    used_chars = 0

    for i, page in enumerate(pages, start=1):
        header = f"[{i}] {page.title}\nURL: {page.url}"
        snippet = snippet_by_url.get(page.url, "")
        body = page.content or snippet
        section = f"{header}\n{body}\n"

        if used_chars + len(section) > MAX_TOTAL_CHARS:
            remaining = MAX_TOTAL_CHARS - used_chars
            if remaining > 200:
                section = truncate_text(section, remaining)
                sections.append(section)
            break

        sections.append(section)
        used_chars += len(section)

    return "\n---\n".join(sections)


def search_web(query: str) -> str:
    """Run a general web search and return cleaned, combined page content."""
    query = query.strip()
    if not query:
        return "Error: query must not be empty."

    cache_key = f"web::{query.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("Cache hit for web search: '%s'", query)
        return cached

    started = time.monotonic()
    logger.info("Search started: web query='%s'", query)

    try:
        hits = _ddgs_text_search(query, SEARCH_MAX_RESULTS)
    except Exception as exc:  # noqa: BLE001 - search provider failure must degrade gracefully
        logger.error("DuckDuckGo text search failed for '%s': %s", query, exc)
        return f"Search failed: could not reach DuckDuckGo ({exc})."

    if not hits:
        logger.info("Search finished: web query='%s' (no results)", query)
        return "No search results found for this query."

    pages = _download_pages(hits, SEARCH_MAX_PAGES)
    formatted = _format_pages(pages, hits)
    cache.set(cache_key, formatted)

    elapsed = time.monotonic() - started
    logger.info("Search finished: web query='%s' in %.2fs", query, elapsed)
    return formatted


def search_news(query: str) -> str:
    """Run a news-focused search and return cleaned, combined page content."""
    query = query.strip()
    if not query:
        return "Error: query must not be empty."

    cache_key = f"news::{query.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("Cache hit for news search: '%s'", query)
        return cached

    started = time.monotonic()
    logger.info("Search started: news query='%s'", query)

    try:
        hits = _ddgs_news_search(query, NEWS_MAX_RESULTS)
    except Exception as exc:  # noqa: BLE001 - search provider failure must degrade gracefully
        logger.error("DuckDuckGo news search failed for '%s': %s", query, exc)
        return f"News search failed: could not reach DuckDuckGo ({exc})."

    if not hits:
        logger.info("Search finished: news query='%s' (no results)", query)
        return "No news results found for this query."

    pages = _download_pages(hits, min(NEWS_MAX_RESULTS, SEARCH_MAX_PAGES))

    # News snippets are often substantial on their own; fall back to them
    # per-item when a page fails to download instead of dropping the item.
    downloaded_urls = {p.url for p in pages}
    for hit in hits:
        if hit.url not in downloaded_urls and hit.snippet:
            date_prefix = f"{hit.date} - " if hit.date else ""
            source_suffix = f" ({hit.source})" if hit.source else ""
            pages.append(
                PageResult(
                    url=hit.url,
                    title=f"{date_prefix}{hit.title}{source_suffix}",
                    content=truncate_text(hit.snippet, MAX_CHARS_PER_PAGE),
                )
            )

    order = {hit.url: i for i, hit in enumerate(hits)}
    pages.sort(key=lambda r: order.get(r.url, 0))

    formatted = _format_pages(pages, hits)
    cache.set(cache_key, formatted)

    elapsed = time.monotonic() - started
    logger.info("Search finished: news query='%s' in %.2fs", query, elapsed)
    return formatted


def fetch_single_page(url: str) -> str:
    """Fetch and clean the readable content of a single URL."""
    url = url.strip()
    if not url:
        return "Error: url must not be empty."

    cache_key = f"page::{url.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("Cache hit for page fetch: '%s'", url)
        return cached

    logger.info("Fetching page: %s", url)
    try:
        content = fetch_and_extract(url)
    except Exception as exc:  # noqa: BLE001 - single-page fetch must degrade gracefully
        logger.error("Unhandled error fetching %s: %s", url, exc)
        return f"Failed to fetch page: {exc}"

    if not content:
        logger.warning("No readable content extracted from %s", url)
        return f"Could not extract readable content from: {url}"

    # A single fetched page is still subject to the per-page character cap
    # so a single very long article cannot blow the model's context budget.
    result = truncate_text(content, MAX_CHARS_PER_PAGE)
    cache.set(cache_key, result)
    return result
