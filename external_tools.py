"""Tools backed by free public APIs: weather, currency rates, Wikipedia.

All providers are keyless and free:

* Weather      - Open-Meteo (geocoding + forecast)
* Currency     - open.er-api.com, with frankfurter.app (ECB) as fallback
* Wikipedia    - MediaWiki search API + REST summary endpoint

Every function returns a human-readable string and degrades to a clear
error message instead of raising. Responses are cached (weather 10 min,
currency 1 h, wiki 1 day by default).
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from cache import cache
from net import get_json
from utils import (
    CURRENCY_TTL_SECONDS,
    WEATHER_TTL_SECONDS,
    WIKI_TTL_SECONDS,
    detect_language,
    get_logger,
    truncate_text,
)

logger = get_logger("external")


def _is_cacheable(result: str) -> bool:
    return not result.startswith(("Error", "Weather lookup failed", "Currency lookup failed",
                                  "Wikipedia lookup failed", "Unknown"))


# --------------------------------------------------------------------------
# Weather (Open-Meteo)
# --------------------------------------------------------------------------

_WMO_CODES: dict[int, str] = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "freezing drizzle", 57: "dense freezing drizzle",
    61: "light rain", 63: "moderate rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snowfall", 73: "moderate snowfall", 75: "heavy snowfall",
    77: "snow grains",
    80: "light rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "light snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}

_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _wind_direction(degrees: Optional[float]) -> str:
    if degrees is None:
        return ""
    return _COMPASS[round(degrees / 45.0) % 8]


def _geocode(location: str) -> Optional[dict]:
    """Resolve a place name to coordinates via Open-Meteo geocoding."""
    lang = detect_language(location)
    data = get_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": "1", "language": lang, "format": "json"},
    )
    if data and data.get("results"):
        return data["results"][0]
    if lang != "en":  # retry in English; the geocoder's EN index is richest
        data = get_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": "1", "language": "en", "format": "json"},
        )
        if data and data.get("results"):
            return data["results"][0]
    return None


def weather(location: str) -> str:
    """Current weather + 3-day forecast for a city (Open-Meteo, no key)."""
    location = location.strip()
    if not location:
        return "Error: location must not be empty."

    def compute() -> str:
        place = _geocode(location)
        if place is None:
            return f"Error: could not find location '{location}'."
        lat, lon = place.get("latitude"), place.get("longitude")
        if lat is None or lon is None:
            return f"Error: no coordinates for '{location}'."

        data = get_json(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": str(lat),
                "longitude": str(lon),
                "current": ("temperature_2m,apparent_temperature,relative_humidity_2m,"
                            "weather_code,wind_speed_10m,wind_direction_10m,"
                            "wind_gusts_10m,precipitation"),
                "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                          "precipitation_probability_max"),
                "forecast_days": "3",
                "timezone": "auto",
                "wind_speed_unit": "ms",
            },
        )
        if not data or "current" not in data:
            return f"Weather lookup failed for '{location}' - forecast service unavailable."

        current = data["current"]
        name = place.get("name", location)
        country = place.get("country", "")
        label = f"{name}, {country}" if country else name

        code_text = _WMO_CODES.get(int(current.get("weather_code", -1)), "unknown conditions")
        wind_dir = _wind_direction(current.get("wind_direction_10m"))
        lines = [
            f"Weather in {label}:",
            f"  Now: {current.get('temperature_2m', '?')}°C "
            f"(feels like {current.get('apparent_temperature', '?')}°C), {code_text}",
            f"  Humidity: {current.get('relative_humidity_2m', '?')}%",
            f"  Wind: {current.get('wind_speed_10m', '?')} m/s {wind_dir}"
            + (f", gusts {current['wind_gusts_10m']} m/s" if current.get("wind_gusts_10m") else ""),
        ]
        if current.get("precipitation"):
            lines.append(f"  Precipitation: {current['precipitation']} mm")

        daily = data.get("daily", {})
        dates = daily.get("time", [])
        if dates:
            lines.append("Forecast:")
            for i, date in enumerate(dates[:3]):
                code = _WMO_CODES.get(int(daily["weather_code"][i]), "?") \
                    if i < len(daily.get("weather_code", [])) else "?"
                tmin = daily.get("temperature_2m_min", ["?"] * 3)[i]
                tmax = daily.get("temperature_2m_max", ["?"] * 3)[i]
                precip = daily.get("precipitation_probability_max", [None] * 3)[i]
                precip_text = f", precip {precip}%" if precip is not None else ""
                lines.append(f"  {date}: {tmin}..{tmax}°C, {code}{precip_text}")
        return "\n".join(lines)

    result, hit = cache.get_or_compute(
        f"weather::{location.lower()}", compute,
        ttl=WEATHER_TTL_SECONDS, cache_predicate=_is_cacheable,
    )
    if hit:
        logger.info("Cache hit for weather: '%s'", location)
    return result


# --------------------------------------------------------------------------
# Currency rates
# --------------------------------------------------------------------------

_CURRENCY_ALIASES: dict[str, str] = {
    "$": "USD", "доллар": "USD", "долларов": "USD", "доллара": "USD", "dollar": "USD",
    "€": "EUR", "евро": "EUR", "euro": "EUR",
    "₽": "RUB", "руб": "RUB", "рубль": "RUB", "рубля": "RUB", "рублей": "RUB", "ruble": "RUB",
    "₺": "TRY", "лира": "TRY", "лиры": "TRY", "лир": "TRY", "lira": "TRY",
    "£": "GBP", "фунт стерлингов": "GBP", "pound": "GBP",
    "¥": "JPY", "иена": "JPY", "иен": "JPY", "yen": "JPY",
    "юань": "CNY", "юаней": "CNY", "yuan": "CNY",
    "грн": "UAH", "гривна": "UAH", "гривен": "UAH", "hryvnia": "UAH",
    "тенге": "KZT", "tenge": "KZT",
    "франк": "CHF", "франков": "CHF", "franc": "CHF",
    "вон": "KRW", "won": "KRW",
    "дирхам": "AED", "dirham": "AED",
    "рупия": "INR", "rupee": "INR",
    "злотый": "PLN", "zloty": "PLN",
}


def _normalize_currency(code: str) -> str:
    cleaned = code.strip().lower().replace("ё", "е")
    alias = _CURRENCY_ALIASES.get(cleaned)
    if alias:
        return alias
    return cleaned.upper()


def _rates_from_erapi(base: str) -> Optional[dict]:
    data = get_json(f"https://open.er-api.com/v6/latest/{quote(base)}")
    if data and data.get("result") == "success" and isinstance(data.get("rates"), dict):
        return {"rates": data["rates"], "updated": data.get("time_last_update_utc", "")[:16],
                "source": "open.er-api.com"}
    return None


def _rates_from_frankfurter(base: str) -> Optional[dict]:
    data = get_json("https://api.frankfurter.app/latest", params={"from": base})
    if data and isinstance(data.get("rates"), dict):
        rates = dict(data["rates"])
        rates[base] = 1.0
        return {"rates": rates, "updated": data.get("date", ""), "source": "frankfurter.app (ECB)"}
    return None


def currency_rate(from_currency: str, to_currency: str, amount: float = 1.0) -> str:
    """Exchange rate and converted amount between two currencies."""
    src = _normalize_currency(from_currency)
    dst = _normalize_currency(to_currency)
    if not (src.isalpha() and len(src) == 3):
        return f"Error: unknown currency '{from_currency}'. Use ISO codes like USD, EUR, RUB."
    if not (dst.isalpha() and len(dst) == 3):
        return f"Error: unknown currency '{to_currency}'. Use ISO codes like USD, EUR, RUB."
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = 1.0

    def compute() -> str:
        table = _rates_from_erapi(src) or _rates_from_frankfurter(src)
        if table is None:
            return f"Currency lookup failed for {src} - rate services unavailable."
        rate = table["rates"].get(dst)
        if rate is None:
            return (f"Error: no rate for {src} -> {dst} "
                    f"(source {table['source']}). Check the currency code.")
        converted = amount * float(rate)
        precision = 4 if converted < 100 else 2
        return (
            f"{amount:g} {src} = {converted:.{precision}f} {dst}\n"
            f"Rate: 1 {src} = {float(rate):.6g} {dst}\n"
            f"Source: {table['source']}, updated {table['updated']}"
        )

    # Cache per base currency+amount pair (rate tables update ~daily).
    result, hit = cache.get_or_compute(
        f"currency::{src}::{dst}::{amount:g}", compute,
        ttl=CURRENCY_TTL_SECONDS, cache_predicate=_is_cacheable,
    )
    if hit:
        logger.info("Cache hit for currency: %s->%s", src, dst)
    return result


# --------------------------------------------------------------------------
# Wikipedia
# --------------------------------------------------------------------------

_WIKI_LANGS = frozenset({
    "en", "ru", "de", "fr", "es", "it", "pt", "tr", "pl", "nl",
    "ja", "ko", "zh", "ar", "he", "th", "el", "uk",
})


def wikipedia_search(query: str, language: str = "") -> str:
    """Search Wikipedia; returns the best article's summary and link."""
    query = query.strip()
    if not query:
        return "Error: query must not be empty."
    lang = language.strip().lower() or detect_language(query)
    if lang not in _WIKI_LANGS:
        lang = "en"

    def compute() -> str:
        found = _wiki_lookup(query, lang)
        if found is None and lang != "en":
            found = _wiki_lookup(query, "en")
        if found is None:
            return f"No Wikipedia article found for '{query}'."
        return found

    result, hit = cache.get_or_compute(
        f"wiki::{lang}::{query.lower()}", compute,
        ttl=WIKI_TTL_SECONDS,
        cache_predicate=lambda r: not r.startswith(("Error", "No Wikipedia", "Wikipedia lookup")),
    )
    if hit:
        logger.info("Cache hit for wikipedia: '%s'", query)
    return result


def _wiki_lookup(query: str, lang: str) -> Optional[str]:
    search_data = get_json(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": "3", "format": "json", "srprop": "",
        },
    )
    if not search_data:
        return None
    matches = search_data.get("query", {}).get("search", [])
    if not matches:
        return None

    title = matches[0].get("title", "")
    summary = get_json(
        f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title, safe='')}"
    )
    if not summary:
        return None

    extract = summary.get("extract", "").strip()
    url = (summary.get("content_urls", {}).get("desktop", {}) or {}).get(
        "page", f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
    )
    description = summary.get("description", "")

    lines = [f"{summary.get('title', title)}"]
    if description:
        lines[0] += f" — {description}"
    if extract:
        lines.append(truncate_text(extract, 1500))
    lines.append(f"URL: {url}")

    others = [m.get("title", "") for m in matches[1:] if m.get("title")]
    if others:
        lines.append(f"Other matches: {', '.join(others)}")
    return "\n".join(lines)
