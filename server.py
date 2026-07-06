"""MCP server entry point: web search + utility tools for AnythingLLM.

Exposes twelve tools over the Model Context Protocol using FastMCP:

Web:      search_web, search_news, fetch_page
Info:     current_time, current_date, weather, currency_rate,
          wikipedia_search
Utility:  unit_converter, calculator, generate_uuid, random_generator

Every tool goes through :func:`_guarded`, which records timing/statistics
and converts any unexpected exception into a readable error string, so a
single failure can never crash the server process AnythingLLM talks to.
"""

from __future__ import annotations

import functools
import time
from typing import Callable

from fastmcp import FastMCP

import external_tools
import local_tools
import search as search_module
from cache import cache
from utils import STATS, get_logger, setup_logging

setup_logging()
logger = get_logger("server")

mcp = FastMCP(
    name="search-mcp",
    instructions=(
        "Provides live internet access (DuckDuckGo/Bing search, news, web "
        "page reading, weather, currency rates, Wikipedia) plus offline "
        "utilities (time, date, unit conversion, calculator, UUID and "
        "random generators). All tools return plain readable text."
    ),
)


def _guarded(fn: Callable[..., str]) -> Callable[..., str]:
    """Wrap a tool: measure timing, collect stats, never raise."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> str:
        started = time.monotonic()
        error = False
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - tool boundary must never raise
            error = True
            logger.exception("%s crashed (args=%r, kwargs=%r)", fn.__name__, args, kwargs)
            return f"{fn.__name__} encountered an unexpected error: {exc}"
        finally:
            elapsed = time.monotonic() - started
            STATS.record_call(fn.__name__, elapsed, error=error)
            logger.info("tool=%s elapsed=%.2fs%s", fn.__name__, elapsed,
                        " (error)" if error else "")

    return wrapper


# --------------------------------------------------------------------------
# Web tools
# --------------------------------------------------------------------------

@mcp.tool
@_guarded
def search_web(query: str) -> str:
    """Search the web and return cleaned content from the top results.

    Runs a cascading search (DuckDuckGo with Bing fallback), downloads up
    to 5 of the best-ranked pages, strips navigation/ads/boilerplate, and
    returns the combined readable text. The search region and language
    are picked automatically from the query language; authoritative
    sources are ranked first. Use for general knowledge, how-to, and
    factual questions that need current information.

    Args:
        query: The search query in natural language (any language).

    Returns:
        Combined readable text of the top results with titles and URLs,
        or a short message if nothing could be retrieved.
    """
    return search_module.search_web(query)


@mcp.tool
@_guarded
def search_news(query: str) -> str:
    """Search recent news and return cleaned article content.

    Uses dedicated news indexes (Bing News, Google News) limited to 5
    results, prefers authoritative outlets, downloads readable article
    text where possible, and falls back to headline + snippet for
    paywalled sources. Use for current events and "latest news about X"
    questions.

    Args:
        query: The news topic in natural language (any language).

    Returns:
        Combined readable news content with dates, sources, and URLs,
        or a short message if nothing could be retrieved.
    """
    return search_module.search_news(query)


@mcp.tool
@_guarded
def fetch_page(url: str) -> str:
    """Fetch one specific webpage and return only its readable content.

    Downloads the page (8s timeout, size-capped), removes navigation,
    ads, cookie banners, and scripts, and returns clean article text.
    Use when you already know the exact URL to read.

    Args:
        url: Full http(s) URL of the page.

    Returns:
        Cleaned readable text, or a short message explaining the failure.
    """
    return search_module.fetch_single_page(url)


# --------------------------------------------------------------------------
# Info tools
# --------------------------------------------------------------------------

@mcp.tool
@_guarded
def current_time(location: str = "") -> str:
    """Current time, date, weekday, and timezone for a place.

    Args:
        location: City, country (English or Russian: "Moscow", "Стамбул",
            "New York"), or IANA zone ("Europe/Moscow"). Empty = server's
            local time.

    Returns:
        Time, date, weekday (EN/RU), timezone name, and UTC offset.
    """
    return local_tools.current_time(location)


@mcp.tool
@_guarded
def current_date(location: str = "") -> str:
    """Current date with weekday, week number, and day of year.

    Args:
        location: City, country, or IANA timezone; empty = server's local
            timezone.

    Returns:
        Date, weekday (EN/RU), month, ISO week number, day of year.
    """
    return local_tools.current_date(location)


@mcp.tool
@_guarded
def weather(location: str) -> str:
    """Current weather and a 3-day forecast for a city (free Open-Meteo).

    Args:
        location: City name in any language ("Moscow", "Стамбул", "Paris").

    Returns:
        Temperature, feels-like, humidity, wind speed/direction, current
        conditions, and a 3-day min/max forecast with precipitation
        probability.
    """
    return external_tools.weather(location)


@mcp.tool
@_guarded
def currency_rate(from_currency: str, to_currency: str, amount: float = 1.0) -> str:
    """Exchange rate between two currencies with optional amount.

    Args:
        from_currency: Source currency - ISO code, symbol, or name
            ("USD", "$", "доллар").
        to_currency: Target currency ("EUR", "RUB", "лира", "₺").
        amount: Amount to convert (default 1).

    Returns:
        Converted amount, the unit rate, data source, and update time.
    """
    return external_tools.currency_rate(from_currency, to_currency, amount)


@mcp.tool
@_guarded
def wikipedia_search(query: str, language: str = "") -> str:
    """Find a Wikipedia article and return its summary and link.

    Args:
        query: Topic to look up (any language; the Wikipedia language
            edition is chosen automatically).
        language: Optional override, e.g. "en" or "ru".

    Returns:
        Article title, short description, summary, URL, and other close
        matches.
    """
    return external_tools.wikipedia_search(query, language)


# --------------------------------------------------------------------------
# Utility tools
# --------------------------------------------------------------------------

@mcp.tool
@_guarded
def unit_converter(value: float, from_unit: str, to_unit: str) -> str:
    """Convert between measurement units.

    Supports mass (kg/lb/oz), length (km/mi/ft), temperature (C/F/K),
    volume (l/gal), area (m2/acre/ha), speed (km/h / mph / knots), data
    (MB/GB), and time. Unit names work in English and Russian
    ("кг", "мили", "литры").

    Args:
        value: Numeric value to convert.
        from_unit: Source unit ("kg", "мили", "°F").
        to_unit: Target unit ("lb", "км", "°C").

    Returns:
        The conversion result, or the list of supported categories if a
        unit is unknown.
    """
    return local_tools.unit_converter(value, from_unit, to_unit)


@mcp.tool
@_guarded
def calculator(expression: str) -> str:
    """Safely evaluate a mathematical expression.

    Supports +, -, *, /, //, ^ or ** (power), N% (percent = N/100),
    parentheses, sqrt/cbrt, trigonometry (sin, cos, tan, atan2...),
    logarithms (log, log2, log10), exp, abs, round, floor, ceil,
    factorial, gcd, mod(a, b), and constants pi, e, tau.

    Args:
        expression: Math expression, e.g. "2^10 + sqrt(144)" or
            "15% * 2400".

    Returns:
        "expression = result", or a clear error message.
    """
    return local_tools.calculator(expression)


@mcp.tool
@_guarded
def generate_uuid(count: int = 1) -> str:
    """Generate random UUIDv4 identifiers.

    Args:
        count: How many UUIDs to generate (1-50, default 1).

    Returns:
        The UUIDs, one per line.
    """
    return local_tools.generate_uuid(count)


@mcp.tool
@_guarded
def random_generator(
    kind: str = "number",
    minimum: float = 1,
    maximum: float = 100,
    length: int = 12,
    options: str = "",
) -> str:
    """Generate random data of a chosen kind.

    Kinds: "number" (integer in [minimum, maximum]), "float", "string"
    (alphanumeric of given length), "password" (secure, mixed classes),
    "coin" (flip), "dice" (options="2d6"), "choice" (pick one of
    options="red,green,blue").

    Args:
        kind: One of number, float, string, password, coin, dice, choice.
        minimum: Lower bound for number/float.
        maximum: Upper bound for number/float.
        length: Length for string/password (8-128 for passwords).
        options: Dice spec ("2d6") or comma-separated choices.

    Returns:
        The generated value as text.
    """
    return local_tools.random_generator(kind, minimum, maximum, length, options)


if __name__ == "__main__":
    logger.info("Starting search-mcp server (stdio transport), %d tools registered", 12)
    try:
        mcp.run()
    finally:
        # Log final statistics and free pooled resources on shutdown.
        STATS.log_summary()
        cache.clear()
