"""Shared utilities for the search-mcp server.

This module centralizes configuration loading, logging setup, an in-memory
TTL cache, and text-cleaning helpers used by both the search and scraper
layers. Keeping these concerns in one small module avoids duplicating
config-parsing or cleanup logic across files.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()


# --------------------------------------------------------------------------
# Configuration (all overridable via .env / environment variables)
# --------------------------------------------------------------------------

def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SEARCH_MAX_RESULTS: int = _get_int("SEARCH_MAX_RESULTS", 5)
SEARCH_MAX_PAGES: int = _get_int("SEARCH_MAX_PAGES", 5)
NEWS_MAX_RESULTS: int = _get_int("NEWS_MAX_RESULTS", 5)
PAGE_TIMEOUT: int = _get_int("PAGE_TIMEOUT", 8)
MAX_CHARS_PER_PAGE: int = _get_int("MAX_CHARS_PER_PAGE", 4000)
MAX_TOTAL_CHARS: int = _get_int("MAX_TOTAL_CHARS", 20000)
CACHE_TTL_SECONDS: int = _get_int("CACHE_TTL_SECONDS", 300)
REQUEST_MAX_WORKERS: int = _get_int("REQUEST_MAX_WORKERS", 4)
SEARCH_RETRY_ATTEMPTS: int = _get_int("SEARCH_RETRY_ATTEMPTS", 3)
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

USER_AGENT: str = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_7_6) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

_LOGGING_CONFIGURED = False


def setup_logging() -> None:
    """Configure structured logging for the whole application.

    Safe to call multiple times; only configures handlers once.
    """
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    level = getattr(logging, LOG_LEVEL, logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger("search_mcp")
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False

    _LOGGING_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger under the ``search_mcp`` hierarchy."""
    setup_logging()
    return logging.getLogger(f"search_mcp.{name}")


# --------------------------------------------------------------------------
# In-memory TTL cache
# --------------------------------------------------------------------------

class TTLCache:
    """A minimal thread-safe in-memory cache with per-entry expiration.

    Intentionally simple: no eviction policy beyond TTL expiry, no
    persistence. This is sufficient for smoothing out repeated identical
    queries within a short window on a single long-lived process.
    """

    def __init__(self, ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.time() >= expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.time() + self._ttl, value)

    def clear_expired(self) -> int:
        """Remove expired entries. Returns the number of entries removed."""
        now = time.time()
        with self._lock:
            expired = [k for k, (exp, _) in self._store.items() if now >= exp]
            for k in expired:
                del self._store[k]
            return len(expired)


cache = TTLCache()


# --------------------------------------------------------------------------
# Text cleaning helpers
# --------------------------------------------------------------------------

_MULTI_BLANK_LINES_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_MULTI_HYPHEN_RE = re.compile(r"-{4,}")

# Lines matching these patterns are almost always menu items, cookie
# notices, or other boilerplate rather than article content.
_BOILERPLATE_LINE_RE = re.compile(
    r"^\s*("
    r"cookie[s]?\s+(policy|consent|notice)|"
    r"accept\s+(all\s+)?cookies|"
    r"sign\s+(in|up)|"
    r"log\s*in|"
    r"subscribe(\s+now)?|"
    r"share\s+(this|on)|"
    r"advertisement|"
    r"skip\s+to\s+(main\s+)?content|"
    r"all\s+rights\s+reserved|"
    r"©\s*\d{4}"
    r")\s*$",
    re.IGNORECASE,
)


def clean_text(raw_text: str) -> str:
    """Normalize whitespace and drop obvious boilerplate lines.

    This is a lightweight second pass applied after extraction (whether via
    trafilatura or BeautifulSoup) to strip leftover navigation/cookie/menu
    fragments and collapse excessive blank lines so the model receives
    dense, readable content rather than noisy HTML remnants.
    """
    if not raw_text:
        return ""

    lines = raw_text.splitlines()
    cleaned_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        if _BOILERPLATE_LINE_RE.match(stripped):
            continue
        if len(stripped) <= 2:
            # Stray bullets, pipes, or single characters left over from menus.
            continue
        cleaned_lines.append(_MULTI_SPACE_RE.sub(" ", stripped))

    text = "\n".join(cleaned_lines)
    text = _MULTI_BLANK_LINES_RE.sub("\n\n", text)
    text = _MULTI_HYPHEN_RE.sub("----", text)
    return text.strip()


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate text to ``max_chars``, cutting on a whitespace boundary."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars * 0.8:
        truncated = truncated[:last_space]
    return truncated.rstrip() + " [...]"


def is_valid_http_url(url: str) -> bool:
    """Return True if ``url`` looks like a fetchable http(s) URL."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
