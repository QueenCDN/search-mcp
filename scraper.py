"""Page downloading and readable-content extraction.

Fetches a URL with ``requests``, then extracts the main readable content
using ``trafilatura`` as the primary parser and a ``BeautifulSoup4``
fallback for pages trafilatura cannot handle. Every failure mode (timeout,
HTTP error, bot-protection challenge, non-HTML content, empty extraction)
is caught and logged so that one bad page never aborts a whole search.
"""

from __future__ import annotations

from typing import Optional

import requests
import trafilatura
from bs4 import BeautifulSoup

from utils import (
    DEFAULT_HEADERS,
    PAGE_TIMEOUT,
    clean_text,
    get_logger,
    is_valid_http_url,
)

logger = get_logger("scraper")

# Tags that never contain the "readable" content of a page.
_NOISE_TAGS = (
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "noscript",
    "iframe",
    "svg",
    "button",
    "input",
)

# Class/id fragments commonly used for cookie banners, ads, and menus.
_NOISE_SELECTORS = (
    "[class*='cookie']",
    "[id*='cookie']",
    "[class*='consent']",
    "[id*='consent']",
    "[class*='banner']",
    "[class*='advert']",
    "[class*='ads']",
    "[id*='ads']",
    "[class*='popup']",
    "[class*='modal']",
    "[class*='menu']",
    "[class*='navbar']",
    "[class*='breadcrumb']",
    "[class*='sidebar']",
    "[class*='social']",
    "[class*='newsletter']",
    "[class*='subscribe']",
    "[role='navigation']",
    "[role='banner']",
    "[role='dialog']",
)

_BOT_CHALLENGE_MARKERS = (
    "just a moment",
    "attention required",
    "cf-browser-verification",
    "checking your browser",
    "enable javascript and cookies",
    "captcha",
)


def fetch_html(url: str, timeout: int = PAGE_TIMEOUT) -> Optional[str]:
    """Download raw HTML for ``url``. Returns ``None`` on any failure.

    All exceptions are caught here: this function is the boundary between
    unreliable network I/O and the rest of the pipeline, which assumes a
    clean ``Optional[str]`` result.
    """
    if not is_valid_http_url(url):
        logger.warning("Skipping invalid URL: %s", url)
        return None

    try:
        response = requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.exceptions.Timeout:
        logger.warning("Timeout after %ss fetching %s", timeout, url)
        return None
    except requests.exceptions.SSLError as exc:
        logger.warning("SSL error fetching %s: %s", url, exc)
        return None
    except requests.exceptions.ConnectionError as exc:
        logger.warning("Connection error fetching %s: %s", url, exc)
        return None
    except requests.exceptions.RequestException as exc:
        logger.warning("Request failed for %s: %s", url, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - last-resort network safety net
        logger.error("Unexpected error fetching %s: %s", url, exc)
        return None

    if response.status_code in (403, 429, 503):
        logger.warning(
            "Fetch blocked for %s (HTTP %s) - likely bot protection, skipping",
            url,
            response.status_code,
        )
        return None
    if response.status_code == 404:
        logger.warning("Page not found (404): %s", url)
        return None
    if not response.ok:
        logger.warning("HTTP error %s for %s", response.status_code, url)
        return None

    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type.lower() and content_type:
        logger.info("Skipping non-HTML content (%s): %s", content_type, url)
        return None

    response.encoding = response.encoding or response.apparent_encoding or "utf-8"
    html = response.text

    lowered_head = html[:2000].lower()
    if any(marker in lowered_head for marker in _BOT_CHALLENGE_MARKERS):
        logger.warning("Bot-protection challenge detected, skipping: %s", url)
        return None

    return html


def _extract_with_trafilatura(html: str, url: str) -> Optional[str]:
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            deduplicate=True,
        )
        return extracted.strip() if extracted else None
    except Exception as exc:  # noqa: BLE001 - extraction must never crash the pipeline
        logger.debug("trafilatura extraction failed for %s: %s", url, exc)
        return None


def _extract_with_bs4(html: str) -> Optional[str]:
    try:
        soup = BeautifulSoup(html, "lxml")

        for tag_name in _NOISE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        for selector in _NOISE_SELECTORS:
            for tag in soup.select(selector):
                tag.decompose()

        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = main.get_text(separator="\n")
        return text.strip() or None
    except Exception as exc:  # noqa: BLE001 - extraction must never crash the pipeline
        logger.error("BeautifulSoup extraction failed: %s", exc)
        return None


def extract_readable_text(html: str, url: str) -> Optional[str]:
    """Extract clean, readable article text from raw HTML.

    Tries trafilatura first (best quality boilerplate removal); falls back
    to a BeautifulSoup-based strip-and-scrape if trafilatura returns
    nothing usable (common on heavily templated or unusual pages).
    """
    text = _extract_with_trafilatura(html, url)
    source = "trafilatura"
    if not text or len(text) < 200:
        fallback_text = _extract_with_bs4(html)
        if fallback_text and (not text or len(fallback_text) > len(text)):
            text = fallback_text
            source = "beautifulsoup"

    if not text:
        return None

    cleaned = clean_text(text)
    if not cleaned:
        return None

    logger.debug("Extracted %d chars from %s via %s", len(cleaned), url, source)
    return cleaned


def fetch_and_extract(url: str, timeout: int = PAGE_TIMEOUT) -> Optional[str]:
    """Fetch ``url`` and return cleaned readable text, or ``None`` on failure."""
    html = fetch_html(url, timeout=timeout)
    if html is None:
        return None
    return extract_readable_text(html, url)
