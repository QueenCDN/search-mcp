"""Search orchestration: provider cascade + concurrent page scraping.

Ties :mod:`providers` (search backends with fallback) to :mod:`scraper`
(page download + extraction) to implement the three web tools exposed
over MCP: general web search, news search, and single-page fetch. Owns
result formatting and query-level caching.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from cache import cache
from providers import SearchHit, news_search, web_search
from scraper import fetch_and_extract
from utils import (
    CACHE_TTL_SECONDS,
    MAX_CHARS_PER_PAGE,
    MAX_TOTAL_CHARS,
    NEWS_MAX_RESULTS,
    PAGE_TTL_SECONDS,
    REQUEST_MAX_WORKERS,
    SEARCH_MAX_PAGES,
    SEARCH_MAX_RESULTS,
    get_logger,
    resolve_region,
    truncate_text,
)

logger = get_logger("search")

# One long-lived pool for page downloads: I/O-bound work, so a small pool
# is enough, and reusing it avoids thread churn on every search.
_EXECUTOR = ThreadPoolExecutor(max_workers=REQUEST_MAX_WORKERS, thread_name_prefix="scrape")

# Do not let one slow batch of pages hold a search hostage: after this many
# seconds the pages that did finish are used and the rest are skipped.
_BATCH_DEADLINE_SECONDS = 20.0

_NO_RESULTS_MESSAGE = "No search results found for this query."
_NO_CONTENT_MESSAGE = "No readable content could be extracted from the search results."


@dataclass
class PageResult:
    """A hit whose readable content is ready for output."""

    url: str
    title: str
    content: str
    source: str = ""
    date: str = ""


def _is_cacheable(result: str) -> bool:
    """Only successful outputs are cached; error strings must be retried."""
    return not (
        result.startswith(("Error:", "Search failed", "News search failed",
                           "Failed to fetch", "Could not extract"))
        or result in (_NO_RESULTS_MESSAGE, _NO_CONTENT_MESSAGE)
    )


def _download_pages(hits: list[SearchHit], max_pages: int) -> list[PageResult]:
    """Fetch and extract up to ``max_pages`` hits concurrently.

    Hits marked ``no_scrape`` (JS shells, hard paywalls, opaque redirect
    links) skip downloading and use their search snippet, so they cost
    nothing and can never fail.
    """
    results: list[PageResult] = []
    to_scrape: list[SearchHit] = []
    for hit in hits[:max_pages]:
        if hit.no_scrape:
            if hit.snippet or hit.source:
                results.append(PageResult(
                    url=hit.url, title=hit.title,
                    content=truncate_text(hit.snippet, MAX_CHARS_PER_PAGE),
                    source=hit.source, date=hit.date,
                ))
        else:
            to_scrape.append(hit)

    downloaded = skipped = 0
    if to_scrape:
        future_to_hit = {
            _EXECUTOR.submit(fetch_and_extract, hit.url): hit for hit in to_scrape
        }
        try:
            for future in as_completed(future_to_hit, timeout=_BATCH_DEADLINE_SECONDS):
                hit = future_to_hit[future]
                try:
                    content = future.result()
                except Exception as exc:  # noqa: BLE001 - one page must not abort the batch
                    logger.warning("Unhandled error extracting %s: %s", hit.url, exc)
                    skipped += 1
                    continue
                if content:
                    results.append(PageResult(
                        url=hit.url, title=hit.title or hit.url,
                        content=truncate_text(content, MAX_CHARS_PER_PAGE),
                        source=hit.source, date=hit.date,
                    ))
                    downloaded += 1
                elif hit.snippet:
                    # Page failed but the search snippet still has value.
                    results.append(PageResult(
                        url=hit.url, title=hit.title,
                        content=truncate_text(hit.snippet, MAX_CHARS_PER_PAGE),
                        source=hit.source, date=hit.date,
                    ))
                    skipped += 1
                else:
                    skipped += 1
        except TimeoutError:
            pending = [f for f in future_to_hit if not f.done()]
            for future in pending:
                future.cancel()
            skipped += len(pending)
            logger.warning("Page batch deadline reached, skipped %d slow pages", len(pending))

    logger.info("Pages downloaded: %d, skipped: %d, snippet-only: %d",
                downloaded, skipped, len(results) - downloaded)

    order = {hit.url: i for i, hit in enumerate(hits)}
    results.sort(key=lambda r: order.get(r.url, len(order)))
    return results


def _format_pages(pages: list[PageResult]) -> str:
    """Combine per-page content into one bounded, model-friendly string."""
    if not pages:
        return _NO_CONTENT_MESSAGE

    sections: list[str] = []
    used = 0
    for i, page in enumerate(pages, start=1):
        meta_bits = [bit for bit in (page.date, page.source) if bit]
        meta = f" ({' | '.join(meta_bits)})" if meta_bits else ""
        section = f"[{i}] {page.title}{meta}\nURL: {page.url}\n{page.content}\n"
        if used + len(section) > MAX_TOTAL_CHARS:
            remaining = MAX_TOTAL_CHARS - used
            if remaining > 200:
                sections.append(truncate_text(section, remaining))
            break
        sections.append(section)
        used += len(section)
    return "\n---\n".join(sections)


def search_web(query: str) -> str:
    """Run a general web search and return cleaned, combined page content."""
    query = query.strip()
    if not query:
        return "Error: query must not be empty."

    def compute() -> str:
        started = time.monotonic()
        region = resolve_region(query)
        logger.info("Search started: web query='%s' region=%s", query, region.ddg_region)
        try:
            hits = web_search(query, region, SEARCH_MAX_RESULTS)
        except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash
            logger.exception("Web search failed for '%s'", query)
            return f"Search failed: {exc}"
        if not hits:
            logger.info("Search finished: web query='%s' (no results)", query)
            return _NO_RESULTS_MESSAGE
        pages = _download_pages(hits, SEARCH_MAX_PAGES)
        formatted = _format_pages(pages)
        logger.info("Search finished: web query='%s' in %.2fs",
                    query, time.monotonic() - started)
        return formatted

    result, hit = cache.get_or_compute(
        f"web::{query.lower()}", compute,
        ttl=CACHE_TTL_SECONDS, cache_predicate=_is_cacheable,
    )
    if hit:
        logger.info("Cache hit for web search: '%s'", query)
    return result


def search_news(query: str) -> str:
    """Run a news-focused search and return cleaned, combined content."""
    query = query.strip()
    if not query:
        return "Error: query must not be empty."

    def compute() -> str:
        started = time.monotonic()
        region = resolve_region(query)
        logger.info("Search started: news query='%s' region=%s", query, region.ddg_region)
        try:
            hits = news_search(query, region, NEWS_MAX_RESULTS)
        except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash
            logger.exception("News search failed for '%s'", query)
            return f"News search failed: {exc}"
        if not hits:
            logger.info("Search finished: news query='%s' (no results)", query)
            return _NO_RESULTS_MESSAGE
        pages = _download_pages(hits, min(NEWS_MAX_RESULTS, SEARCH_MAX_PAGES))
        formatted = _format_pages(pages)
        logger.info("Search finished: news query='%s' in %.2fs",
                    query, time.monotonic() - started)
        return formatted

    result, hit = cache.get_or_compute(
        f"news::{query.lower()}", compute,
        ttl=CACHE_TTL_SECONDS, cache_predicate=_is_cacheable,
    )
    if hit:
        logger.info("Cache hit for news search: '%s'", query)
    return result


def fetch_single_page(url: str) -> str:
    """Fetch and clean the readable content of a single URL."""
    url = url.strip()
    if not url:
        return "Error: url must not be empty."

    def compute() -> str:
        logger.info("Fetching page: %s", url)
        try:
            content = fetch_and_extract(url)
        except Exception as exc:  # noqa: BLE001 - degrade gracefully, never crash
            logger.exception("Unhandled error fetching %s", url)
            return f"Failed to fetch page: {exc}"
        if not content:
            logger.info("No readable content extracted from %s", url)
            return f"Could not extract readable content from: {url}"
        # A single page still respects the per-page cap so one long article
        # cannot blow the model's context budget.
        return truncate_text(content, MAX_CHARS_PER_PAGE)

    result, hit = cache.get_or_compute(
        f"page::{url.lower()}", compute,
        ttl=PAGE_TTL_SECONDS, cache_predicate=_is_cacheable,
    )
    if hit:
        logger.info("Cache hit for page fetch: '%s'", url)
    return result
