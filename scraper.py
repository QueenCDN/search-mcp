"""Page downloading and readable-content extraction.

Downloads pages through the pooled HTTP layer in :mod:`net` (bounded
size, connection reuse) and extracts the main readable content using
``trafilatura`` as the primary parser with a ``BeautifulSoup4`` fallback.
Every failure mode (timeout, HTTP error, bot challenge, non-HTML content,
empty extraction) returns ``None`` instead of raising, so one bad page
never aborts a whole search.
"""

from __future__ import annotations

import re
from typing import Optional

import trafilatura
from bs4 import BeautifulSoup

from net import http_get
from utils import PAGE_TIMEOUT, clean_text, get_logger, is_valid_http_url

logger = get_logger("scraper")

# Tags that never contain the readable content of a page.
_NOISE_TAGS = (
    "script", "style", "nav", "header", "footer", "aside", "form",
    "noscript", "iframe", "svg", "button", "input", "select", "dialog",
    "figure", "video", "audio",
)

# Class/id/role fragments used by cookie banners, ads, menus, popups.
_NOISE_SELECTORS = (
    "[class*='cookie']", "[id*='cookie']",
    "[class*='consent']", "[id*='consent']", "[class*='gdpr']",
    "[class*='banner']", "[class*='advert']", "[class*='adsbox']",
    "[class*='ads-']", "[id*='google_ads']", "[class*='sponsor']",
    "[class*='popup']", "[class*='modal']", "[class*='overlay']",
    "[class*='paywall']", "[class*='subscribe']", "[class*='newsletter']",
    "[class*='menu']", "[class*='navbar']", "[class*='breadcrumb']",
    "[class*='sidebar']", "[class*='related']", "[class*='recommend']",
    "[class*='share']", "[class*='social']", "[class*='comment']",
    "[class*='promo']", "[class*='widget']", "[class*='pagination']",
    "[role='navigation']", "[role='banner']", "[role='dialog']",
    "[role='complementary']", "[aria-hidden='true']",
)

# Specific challenge-page phrases only: generic words like "captcha" appear
# in ordinary pages' scripts (Wikipedia's config JS, articles about bots)
# and must not cause false positives.
_BOT_CHALLENGE_TITLES = (
    "just a moment", "attention required", "access denied",
    "are you a robot", "verify you are human", "security check",
)
_BOT_CHALLENGE_MARKERS = (
    "cf-browser-verification", "cf_chl_opt", "_cf_chl", "g-recaptcha\"",
    "checking your browser before accessing",
    "enable javascript and cookies to continue",
    "ddos protection by",
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _page_looks_blocked(page: str) -> bool:
    head = page[:6000].lower()
    match = _TITLE_RE.search(head)
    if match and any(phrase in match.group(1) for phrase in _BOT_CHALLENGE_TITLES):
        return True
    return any(marker in head for marker in _BOT_CHALLENGE_MARKERS)


def fetch_html(url: str, timeout: float = PAGE_TIMEOUT) -> Optional[str]:
    """Download HTML for ``url``. Returns ``None`` on any failure."""
    if not is_valid_http_url(url):
        logger.warning("Skipping invalid URL: %s", url)
        return None

    result = http_get(url, timeout=timeout)
    if not result.ok:
        level = logger.info if result.blocked or result.status == 404 else logger.warning
        level("Fetch failed for %s: %s", url, result.error or f"HTTP {result.status}")
        return None

    content_type = result.content_type.lower()
    if content_type and "html" not in content_type:
        if content_type.startswith("text/"):
            # Plain-text resources are already "readable content".
            return None if not result.text.strip() else result.text
        logger.info("Skipping non-HTML content (%s): %s", result.content_type, url)
        return None

    if _page_looks_blocked(result.text):
        logger.info("Bot-protection challenge detected, skipping: %s", url)
        return None
    return result.text


def _extract_with_trafilatura(page: str, url: str) -> Optional[str]:
    try:
        extracted = trafilatura.extract(
            page,
            url=url,
            include_comments=False,
            # Tables (infoboxes, spec sheets) extract as noisy pipe rows
            # that waste the model's small context; prose only.
            include_tables=False,
            favor_precision=True,
            deduplicate=True,
        )
        return extracted.strip() if extracted else None
    except Exception as exc:  # noqa: BLE001 - extraction must never crash the pipeline
        logger.debug("trafilatura extraction failed for %s: %s", url, exc)
        return None


def _extract_with_bs4(page: str) -> Optional[str]:
    try:
        soup = BeautifulSoup(page, "lxml")
        for tag_name in _NOISE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()
        for selector in _NOISE_SELECTORS:
            try:
                for tag in soup.select(selector):
                    tag.decompose()
            except Exception:  # noqa: BLE001 - one bad selector must not stop the rest
                continue
        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find(attrs={"role": "main"})
            or soup.body
            or soup
        )
        text = main.get_text(separator="\n")
        return text.strip() or None
    except Exception as exc:  # noqa: BLE001 - extraction must never crash the pipeline
        logger.warning("BeautifulSoup extraction failed: %s", exc)
        return None


def extract_readable_text(page: str, url: str) -> Optional[str]:
    """Extract clean readable article text from raw HTML.

    Plain-text input (from text/plain resources) passes straight through
    cleanup. For HTML, trafilatura runs first (best boilerplate removal);
    if it fails or returns something suspiciously short, the
    BeautifulSoup strip-and-scrape fallback is tried and the better of
    the two results wins.
    """
    if "<" not in page[:1000]:
        return clean_text(page) or None

    text = _extract_with_trafilatura(page, url)
    source = "trafilatura"
    if not text or len(text) < 200:
        fallback = _extract_with_bs4(page)
        if fallback and (not text or len(fallback) > len(text)):
            text, source = fallback, "beautifulsoup"

    if not text:
        return None
    cleaned = clean_text(text)
    if not cleaned:
        return None
    logger.debug("Extracted %d chars from %s via %s", len(cleaned), url, source)
    return cleaned


def fetch_and_extract(url: str, timeout: float = PAGE_TIMEOUT) -> Optional[str]:
    """Fetch ``url`` and return cleaned readable text, or ``None``."""
    page = fetch_html(url, timeout=timeout)
    if page is None:
        return None
    return extract_readable_text(page, url)
