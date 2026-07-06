"""Shared utilities for the search-mcp server.

Centralizes configuration loading, colored structured logging, runtime
statistics, language/region detection, and text-cleaning helpers used by
the search, scraper, and tool layers.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from typing import Optional

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


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Search limits
SEARCH_MAX_RESULTS: int = _get_int("SEARCH_MAX_RESULTS", 5)
SEARCH_MAX_PAGES: int = _get_int("SEARCH_MAX_PAGES", 5)
NEWS_MAX_RESULTS: int = _get_int("NEWS_MAX_RESULTS", 5)
SEARCH_RETRY_ATTEMPTS: int = _get_int("SEARCH_RETRY_ATTEMPTS", 2)

# Region: "auto" derives it from the query language; a fixed region code
# (e.g. "ru-ru", "us-en", "de-de") forces every search to that region.
SEARCH_REGION: str = os.getenv("SEARCH_REGION", "auto").strip().lower()

# Networking
PAGE_TIMEOUT: int = _get_int("PAGE_TIMEOUT", 8)
REQUEST_MAX_WORKERS: int = _get_int("REQUEST_MAX_WORKERS", 4)
MAX_HTML_BYTES: int = _get_int("MAX_HTML_BYTES", 2_000_000)
PROVIDER_MIN_INTERVAL: float = _get_float("PROVIDER_MIN_INTERVAL", 2.0)
PROVIDER_COOLDOWN: int = _get_int("PROVIDER_COOLDOWN", 120)

# Content limits
MAX_CHARS_PER_PAGE: int = _get_int("MAX_CHARS_PER_PAGE", 4000)
MAX_TOTAL_CHARS: int = _get_int("MAX_TOTAL_CHARS", 20000)

# Cache
CACHE_TTL_SECONDS: int = _get_int("CACHE_TTL_SECONDS", 300)
CACHE_MAX_ENTRIES: int = _get_int("CACHE_MAX_ENTRIES", 256)
CACHE_MAX_MEMORY_MB: int = _get_int("CACHE_MAX_MEMORY_MB", 32)
WEATHER_TTL_SECONDS: int = _get_int("WEATHER_TTL_SECONDS", 600)
CURRENCY_TTL_SECONDS: int = _get_int("CURRENCY_TTL_SECONDS", 3600)
WIKI_TTL_SECONDS: int = _get_int("WIKI_TTL_SECONDS", 86400)
PAGE_TTL_SECONDS: int = _get_int("PAGE_TTL_SECONDS", 900)

# Misc
DEFAULT_TIMEZONE: str = os.getenv("DEFAULT_TIMEZONE", "").strip()
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
STATS_LOG_EVERY: int = _get_int("STATS_LOG_EVERY", 20)

USER_AGENT: str = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_7_6) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)


# --------------------------------------------------------------------------
# Logging (colored, structured)
# --------------------------------------------------------------------------

_ANSI_COLORS = {
    logging.DEBUG: "\x1b[36m",     # cyan
    logging.INFO: "\x1b[32m",      # green
    logging.WARNING: "\x1b[33m",   # yellow
    logging.ERROR: "\x1b[31m",     # red
    logging.CRITICAL: "\x1b[1;31m",  # bold red
}
_ANSI_RESET = "\x1b[0m"
_ANSI_DIM = "\x1b[2m"


def _color_enabled() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("FORCE_COLOR"):
        return True
    if not sys.stderr.isatty():
        return False
    if sys.platform == "win32":
        # Modern Windows terminals set one of these; classic conhost may not
        # render ANSI, so stay conservative there.
        return bool(os.getenv("WT_SESSION") or os.getenv("ANSICON") or os.getenv("TERM"))
    return True


class _ColorFormatter(logging.Formatter):
    """Log formatter with per-level ANSI colors when the stream supports it."""

    def __init__(self, use_color: bool) -> None:
        super().__init__(datefmt="%H:%M:%S")
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        level = f"{record.levelname:<8}"
        name = record.name.removeprefix("search_mcp.")
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        if self._use_color:
            color = _ANSI_COLORS.get(record.levelno, "")
            return (
                f"{_ANSI_DIM}{timestamp}{_ANSI_RESET} "
                f"{color}{level}{_ANSI_RESET} "
                f"{_ANSI_DIM}{name:<10}{_ANSI_RESET} {message}"
            )
        return f"{timestamp} | {level} | {name:<10} | {message}"


_LOGGING_CONFIGURED = False


def setup_logging() -> None:
    """Configure logging once for the whole application (idempotent)."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    level = getattr(logging, LOG_LEVEL, logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_ColorFormatter(_color_enabled()))

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
# Runtime statistics
# --------------------------------------------------------------------------

def _process_memory_mb() -> Optional[float]:
    """Best-effort resident memory of this process in MB (None if unknown)."""
    try:
        import resource  # Unix only (macOS/Linux); absent on Windows

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes, Linux reports kilobytes.
        divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        return round(rss / divisor, 1)
    except ImportError:
        try:
            import ctypes
            import ctypes.wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.wintypes.DWORD),
                    ("PageFaultCount", ctypes.wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                return round(pmc.WorkingSetSize / (1024 * 1024), 1)
        except Exception:  # noqa: BLE001 - stats are best-effort only
            pass
    except Exception:  # noqa: BLE001 - stats are best-effort only
        pass
    return None


class StatsCollector:
    """Thread-safe counters for tool calls, errors, cache hits, and timing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, int] = {}
        self._errors: dict[str, int] = {}
        self._total_time: dict[str, float] = {}
        self._cache_hits = 0
        self._started = time.time()
        self._logger = get_logger("stats")

    def record_call(self, tool: str, elapsed: float, error: bool = False) -> None:
        with self._lock:
            self._calls[tool] = self._calls.get(tool, 0) + 1
            self._total_time[tool] = self._total_time.get(tool, 0.0) + elapsed
            if error:
                self._errors[tool] = self._errors.get(tool, 0) + 1
            total_calls = sum(self._calls.values())
        if STATS_LOG_EVERY > 0 and total_calls % STATS_LOG_EVERY == 0:
            self.log_summary()

    def record_cache_hit(self) -> None:
        with self._lock:
            self._cache_hits += 1

    def snapshot(self) -> dict:
        with self._lock:
            total_calls = sum(self._calls.values())
            total_errors = sum(self._errors.values())
            per_tool = {
                tool: {
                    "calls": count,
                    "errors": self._errors.get(tool, 0),
                    "avg_seconds": round(self._total_time[tool] / count, 3),
                }
                for tool, count in sorted(self._calls.items())
            }
            return {
                "uptime_seconds": round(time.time() - self._started, 1),
                "total_calls": total_calls,
                "total_errors": total_errors,
                "cache_hits": self._cache_hits,
                "memory_mb": _process_memory_mb(),
                "tools": per_tool,
            }

    def log_summary(self) -> None:
        snap = self.snapshot()
        memory = f", memory={snap['memory_mb']}MB" if snap["memory_mb"] is not None else ""
        self._logger.info(
            "stats: %d calls, %d errors, %d cache hits, uptime=%.0fs%s",
            snap["total_calls"],
            snap["total_errors"],
            snap["cache_hits"],
            snap["uptime_seconds"],
            memory,
        )


STATS = StatsCollector()


# --------------------------------------------------------------------------
# Language and region detection
# --------------------------------------------------------------------------

# Non-Latin scripts identify a language definitively.
_SCRIPT_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[Ѐ-ӿ]"), "ru"),          # Cyrillic
    (re.compile(r"[぀-ヿ]"), "ja"),          # Hiragana/Katakana
    (re.compile(r"[가-힯]"), "ko"),          # Hangul
    (re.compile(r"[一-鿿]"), "zh"),          # CJK ideographs
    (re.compile(r"[؀-ۿ]"), "ar"),          # Arabic
    (re.compile(r"[֐-׿]"), "he"),          # Hebrew
    (re.compile(r"[฀-๿]"), "th"),          # Thai
    (re.compile(r"[Ͱ-Ͽ]"), "el"),          # Greek
)

# Latin diacritics are only a weak hint, consulted after the stop-word vote.
_DIACRITIC_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[ğışİĞŞ]"), "tr"),
    (re.compile(r"[ąęłńśźż]"), "pl"),
    (re.compile(r"[äöüß]"), "de"),
    (re.compile(r"[çœèêëîïôû]"), "fr"),
    (re.compile(r"[ñ¿¡]"), "es"),
    (re.compile(r"[ãõ]"), "pt"),
)

# Frequent function words for Latin-script languages (lowercase).
_STOPWORD_HINTS: dict[str, frozenset[str]] = {
    "de": frozenset("der die das und ist nicht ein eine mit für von wie was wer wo".split()),
    "fr": frozenset("le la les et est pas une des pour que qui dans quoi comment".split()),
    "es": frozenset("el la los las y es no una para que como donde cuando por".split()),
    "it": frozenset("il lo la gli e che non una per come dove quando sono".split()),
    "pt": frozenset("o os as e que nao uma para como onde quando com por".split()),
    "tr": frozenset("bir ve bu ne nasil nerede ile icin mi degil var yok".split()),
    "nl": frozenset("de het een en niet voor met van hoe wat waar wie".split()),
    "pl": frozenset("i nie to jak co gdzie kiedy jest dla przez oraz czy".split()),
}


def detect_language(text: str) -> str:
    """Best-effort ISO 639-1 language code for a query. Defaults to 'en'.

    Deliberately dependency-free: character-script detection covers
    non-Latin languages precisely, and a small stop-word vote separates
    the major Latin-script languages. Short ambiguous queries fall back
    to English, which every search backend handles well.
    """
    if not text:
        return "en"
    for pattern, lang in _SCRIPT_HINTS:
        if pattern.search(text):
            return lang

    words = frozenset(re.findall(r"[a-zà-ÿğışą-ż']+", text.lower()))
    if words:
        best_lang, best_score = "en", 0
        for lang, stopwords in _STOPWORD_HINTS.items():
            score = len(words & stopwords)
            if score > best_score:
                best_lang, best_score = lang, score
        if best_score >= 2:
            return best_lang

    for pattern, lang in _DIACRITIC_HINTS:
        if pattern.search(text):
            return lang
    return "en"


# language -> (ddg region "kl", bing market "mkt", country code, google lang)
_REGION_MAP: dict[str, tuple[str, str, str]] = {
    "en": ("us-en", "en-US", "US"),
    "ru": ("ru-ru", "ru-RU", "RU"),
    "de": ("de-de", "de-DE", "DE"),
    "fr": ("fr-fr", "fr-FR", "FR"),
    "es": ("es-es", "es-ES", "ES"),
    "it": ("it-it", "it-IT", "IT"),
    "pt": ("br-pt", "pt-BR", "BR"),
    "tr": ("tr-tr", "tr-TR", "TR"),
    "pl": ("pl-pl", "pl-PL", "PL"),
    "nl": ("nl-nl", "nl-NL", "NL"),
    "ja": ("jp-jp", "ja-JP", "JP"),
    "ko": ("kr-kr", "ko-KR", "KR"),
    "zh": ("cn-zh", "zh-CN", "CN"),
    "ar": ("xa-ar", "ar-SA", "SA"),
    "he": ("il-he", "he-IL", "IL"),
    "th": ("th-th", "th-TH", "TH"),
    "el": ("gr-el", "el-GR", "GR"),
}

# ddg region -> (language, bing market, country) for explicit SEARCH_REGION.
_DDG_REGION_REVERSE: dict[str, tuple[str, str, str]] = {
    ddg: (lang, mkt, country) for lang, (ddg, mkt, country) in _REGION_MAP.items()
}


class SearchRegion:
    """Resolved search-region parameters for all backends."""

    __slots__ = ("language", "ddg_region", "bing_market", "country")

    def __init__(self, language: str, ddg_region: str, bing_market: str, country: str) -> None:
        self.language = language
        self.ddg_region = ddg_region
        self.bing_market = bing_market
        self.country = country

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SearchRegion({self.language}, {self.ddg_region})"


def resolve_region(query: str) -> SearchRegion:
    """Pick search-region parameters from config or from the query language.

    ``SEARCH_REGION=auto`` (default): the query's language decides — a
    Russian query searches the Russian region, an English query the US
    region, etc. A fixed ``SEARCH_REGION`` (e.g. ``ru-ru``) pins the
    region for every query while the interface language still follows
    the query.
    """
    lang = detect_language(query)
    if SEARCH_REGION and SEARCH_REGION != "auto":
        pinned = _DDG_REGION_REVERSE.get(SEARCH_REGION)
        if pinned:
            _, mkt, country = pinned
            return SearchRegion(lang, SEARCH_REGION, mkt, country)
    ddg, mkt, country = _REGION_MAP.get(lang, _REGION_MAP["en"])
    return SearchRegion(lang, ddg, mkt, country)


# --------------------------------------------------------------------------
# Text cleaning helpers
# --------------------------------------------------------------------------

_MULTI_BLANK_LINES_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_ONLY_PUNCT_RE = re.compile(r"^[\W_]+$")

# Lines matching these are almost always cookie banners, menus, share
# widgets, or other boilerplate rather than article content (EN + RU).
_BOILERPLATE_LINE_RE = re.compile(
    r"^\s*("
    r"cookie[s]?\s+(policy|consent|notice|settings)|"
    r"(accept|reject|manage)\s+(all\s+)?cookies|"
    r"we\s+use\s+cookies.*|"
    r"sign\s+(in|up)|log\s*in|"
    r"subscribe(\s+now|\s+to.*)?|"
    r"share\s+(this\s+)?(article|story|on|via).*|"
    r"follow\s+us.*|"
    r"advertisement|sponsored(\s+content)?|"
    r"skip\s+to\s+(main\s+)?content|"
    r"read\s+(more|next|also).*|"
    r"related\s+(articles|stories|posts)|"
    r"all\s+rights\s+reserved.*|"
    r"©\s*\d{4}.*|"
    r"загрузка\.*|"
    r"(принять|отклонить)\s+(все\s+)?(файлы\s+)?cookie.*|"
    r"мы\s+используем\s+(файлы\s+)?cookie.*|"
    r"(войти|вход|регистрация|зарегистрироваться)|"
    r"подпис(аться|ка|ывайтесь).*|"
    r"поделиться.*|"
    r"читайте\s+(также|далее|ещё|еще).*|"
    r"реклама|"
    r"все\s+права\s+защищены.*"
    r")\s*$",
    re.IGNORECASE,
)


def clean_text(raw_text: str) -> str:
    """Normalize whitespace and drop boilerplate/duplicate lines.

    Applied after extraction (trafilatura or BeautifulSoup) to remove
    leftover navigation/cookie/menu fragments, collapse blank runs, and
    de-duplicate repeated lines so the model receives dense readable
    content.
    """
    if not raw_text:
        return ""

    cleaned_lines: list[str] = []
    seen: set[str] = set()
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if len(stripped) <= 2 or _ONLY_PUNCT_RE.match(stripped):
            continue
        if _BOILERPLATE_LINE_RE.match(stripped):
            continue
        normalized = _MULTI_SPACE_RE.sub(" ", stripped)
        # Drop exact repeats of short lines (menus and footers repeat them;
        # legitimate prose lines are long enough to keep even when equal).
        key = normalized.lower()
        if len(normalized) < 80:
            if key in seen:
                continue
            seen.add(key)
        cleaned_lines.append(normalized)

    text = "\n".join(cleaned_lines)
    text = _MULTI_BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate to ``max_chars``, cutting on a whitespace boundary."""
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


def domain_of(url: str) -> str:
    """Lowercased registrable-ish host of a URL, with 'www.' stripped."""
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).netloc or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.").split(":")[0]
