"""Offline utility tools: time, date, unit conversion, math, randomness.

Everything in this module works without network access, so these tools
are instant and can never fail due to connectivity. City/country names
accept both English and Russian spellings.
"""

from __future__ import annotations

import ast
import math
import operator
import re
import secrets
import string
import uuid as uuid_module
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from utils import DEFAULT_TIMEZONE


# --------------------------------------------------------------------------
# Timezone resolution (city/country -> IANA zone), EN + RU aliases
# --------------------------------------------------------------------------

_CITY_TIMEZONES: dict[str, str] = {
    # Russia & CIS
    "moscow": "Europe/Moscow", "москва": "Europe/Moscow",
    "saint petersburg": "Europe/Moscow", "санкт-петербург": "Europe/Moscow",
    "спб": "Europe/Moscow", "петербург": "Europe/Moscow",
    "novosibirsk": "Asia/Novosibirsk", "новосибирск": "Asia/Novosibirsk",
    "yekaterinburg": "Asia/Yekaterinburg", "екатеринбург": "Asia/Yekaterinburg",
    "kazan": "Europe/Moscow", "казань": "Europe/Moscow",
    "sochi": "Europe/Moscow", "сочи": "Europe/Moscow",
    "vladivostok": "Asia/Vladivostok", "владивосток": "Asia/Vladivostok",
    "kaliningrad": "Europe/Kaliningrad", "калининград": "Europe/Kaliningrad",
    "russia": "Europe/Moscow", "россия": "Europe/Moscow",
    "kyiv": "Europe/Kyiv", "kiev": "Europe/Kyiv", "киев": "Europe/Kyiv",
    "ukraine": "Europe/Kyiv", "украина": "Europe/Kyiv",
    "minsk": "Europe/Minsk", "минск": "Europe/Minsk",
    "belarus": "Europe/Minsk", "беларусь": "Europe/Minsk",
    "almaty": "Asia/Almaty", "алматы": "Asia/Almaty",
    "astana": "Asia/Almaty", "астана": "Asia/Almaty",
    "kazakhstan": "Asia/Almaty", "казахстан": "Asia/Almaty",
    "tashkent": "Asia/Tashkent", "ташкент": "Asia/Tashkent",
    "uzbekistan": "Asia/Tashkent", "узбекистан": "Asia/Tashkent",
    "yerevan": "Asia/Yerevan", "ереван": "Asia/Yerevan",
    "armenia": "Asia/Yerevan", "армения": "Asia/Yerevan",
    "tbilisi": "Asia/Tbilisi", "тбилиси": "Asia/Tbilisi",
    "georgia": "Asia/Tbilisi", "грузия": "Asia/Tbilisi",
    "baku": "Asia/Baku", "баку": "Asia/Baku",
    "azerbaijan": "Asia/Baku", "азербайджан": "Asia/Baku",
    "bishkek": "Asia/Bishkek", "бишкек": "Asia/Bishkek",
    "chisinau": "Europe/Chisinau", "кишинев": "Europe/Chisinau", "кишинёв": "Europe/Chisinau",
    # Europe
    "london": "Europe/London", "лондон": "Europe/London",
    "uk": "Europe/London", "великобритания": "Europe/London", "англия": "Europe/London",
    "paris": "Europe/Paris", "париж": "Europe/Paris",
    "france": "Europe/Paris", "франция": "Europe/Paris",
    "berlin": "Europe/Berlin", "берлин": "Europe/Berlin",
    "germany": "Europe/Berlin", "германия": "Europe/Berlin",
    "munich": "Europe/Berlin", "мюнхен": "Europe/Berlin",
    "madrid": "Europe/Madrid", "мадрид": "Europe/Madrid",
    "spain": "Europe/Madrid", "испания": "Europe/Madrid",
    "barcelona": "Europe/Madrid", "барселона": "Europe/Madrid",
    "rome": "Europe/Rome", "рим": "Europe/Rome",
    "italy": "Europe/Rome", "италия": "Europe/Rome",
    "milan": "Europe/Rome", "милан": "Europe/Rome",
    "amsterdam": "Europe/Amsterdam", "амстердам": "Europe/Amsterdam",
    "netherlands": "Europe/Amsterdam", "нидерланды": "Europe/Amsterdam",
    "brussels": "Europe/Brussels", "брюссель": "Europe/Brussels",
    "vienna": "Europe/Vienna", "вена": "Europe/Vienna",
    "austria": "Europe/Vienna", "австрия": "Europe/Vienna",
    "zurich": "Europe/Zurich", "цюрих": "Europe/Zurich",
    "switzerland": "Europe/Zurich", "швейцария": "Europe/Zurich",
    "geneva": "Europe/Zurich", "женева": "Europe/Zurich",
    "stockholm": "Europe/Stockholm", "стокгольм": "Europe/Stockholm",
    "sweden": "Europe/Stockholm", "швеция": "Europe/Stockholm",
    "oslo": "Europe/Oslo", "осло": "Europe/Oslo",
    "norway": "Europe/Oslo", "норвегия": "Europe/Oslo",
    "helsinki": "Europe/Helsinki", "хельсинки": "Europe/Helsinki",
    "finland": "Europe/Helsinki", "финляндия": "Europe/Helsinki",
    "copenhagen": "Europe/Copenhagen", "копенгаген": "Europe/Copenhagen",
    "denmark": "Europe/Copenhagen", "дания": "Europe/Copenhagen",
    "warsaw": "Europe/Warsaw", "варшава": "Europe/Warsaw",
    "poland": "Europe/Warsaw", "польша": "Europe/Warsaw",
    "prague": "Europe/Prague", "прага": "Europe/Prague",
    "czechia": "Europe/Prague", "чехия": "Europe/Prague",
    "budapest": "Europe/Budapest", "будапешт": "Europe/Budapest",
    "hungary": "Europe/Budapest", "венгрия": "Europe/Budapest",
    "bucharest": "Europe/Bucharest", "бухарест": "Europe/Bucharest",
    "romania": "Europe/Bucharest", "румыния": "Europe/Bucharest",
    "sofia": "Europe/Sofia", "софия": "Europe/Sofia",
    "bulgaria": "Europe/Sofia", "болгария": "Europe/Sofia",
    "belgrade": "Europe/Belgrade", "белград": "Europe/Belgrade",
    "serbia": "Europe/Belgrade", "сербия": "Europe/Belgrade",
    "athens": "Europe/Athens", "афины": "Europe/Athens",
    "greece": "Europe/Athens", "греция": "Europe/Athens",
    "lisbon": "Europe/Lisbon", "лиссабон": "Europe/Lisbon",
    "portugal": "Europe/Lisbon", "португалия": "Europe/Lisbon",
    "dublin": "Europe/Dublin", "дублин": "Europe/Dublin",
    "ireland": "Europe/Dublin", "ирландия": "Europe/Dublin",
    "riga": "Europe/Riga", "рига": "Europe/Riga", "latvia": "Europe/Riga",
    "vilnius": "Europe/Vilnius", "вильнюс": "Europe/Vilnius",
    "tallinn": "Europe/Tallinn", "таллин": "Europe/Tallinn",
    # Turkey & Middle East
    "istanbul": "Europe/Istanbul", "стамбул": "Europe/Istanbul",
    "ankara": "Europe/Istanbul", "анкара": "Europe/Istanbul",
    "antalya": "Europe/Istanbul", "анталья": "Europe/Istanbul", "анталия": "Europe/Istanbul",
    "izmir": "Europe/Istanbul", "измир": "Europe/Istanbul",
    "turkey": "Europe/Istanbul", "турция": "Europe/Istanbul",
    "dubai": "Asia/Dubai", "дубай": "Asia/Dubai",
    "uae": "Asia/Dubai", "оаэ": "Asia/Dubai",
    "abu dhabi": "Asia/Dubai", "абу-даби": "Asia/Dubai",
    "doha": "Asia/Qatar", "доха": "Asia/Qatar", "qatar": "Asia/Qatar", "катар": "Asia/Qatar",
    "riyadh": "Asia/Riyadh", "эр-рияд": "Asia/Riyadh",
    "saudi arabia": "Asia/Riyadh", "саудовская аравия": "Asia/Riyadh",
    "tel aviv": "Asia/Jerusalem", "тель-авив": "Asia/Jerusalem",
    "jerusalem": "Asia/Jerusalem", "иерусалим": "Asia/Jerusalem",
    "israel": "Asia/Jerusalem", "израиль": "Asia/Jerusalem",
    "tehran": "Asia/Tehran", "тегеран": "Asia/Tehran", "iran": "Asia/Tehran", "иран": "Asia/Tehran",
    "cairo": "Africa/Cairo", "каир": "Africa/Cairo", "egypt": "Africa/Cairo", "египет": "Africa/Cairo",
    # Americas
    "new york": "America/New_York", "нью-йорк": "America/New_York",
    "usa": "America/New_York", "сша": "America/New_York",
    "washington": "America/New_York", "вашингтон": "America/New_York",
    "boston": "America/New_York", "бостон": "America/New_York",
    "miami": "America/New_York", "майами": "America/New_York",
    "chicago": "America/Chicago", "чикаго": "America/Chicago",
    "dallas": "America/Chicago", "даллас": "America/Chicago",
    "denver": "America/Denver", "денвер": "America/Denver",
    "los angeles": "America/Los_Angeles", "лос-анджелес": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "сан-франциско": "America/Los_Angeles",
    "seattle": "America/Los_Angeles", "сиэтл": "America/Los_Angeles",
    "toronto": "America/Toronto", "торонто": "America/Toronto",
    "canada": "America/Toronto", "канада": "America/Toronto",
    "vancouver": "America/Vancouver", "ванкувер": "America/Vancouver",
    "mexico city": "America/Mexico_City", "мехико": "America/Mexico_City",
    "mexico": "America/Mexico_City", "мексика": "America/Mexico_City",
    "sao paulo": "America/Sao_Paulo", "сан-паулу": "America/Sao_Paulo",
    "brazil": "America/Sao_Paulo", "бразилия": "America/Sao_Paulo",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "буэнос-айрес": "America/Argentina/Buenos_Aires",
    "argentina": "America/Argentina/Buenos_Aires", "аргентина": "America/Argentina/Buenos_Aires",
    # Asia & Oceania
    "tokyo": "Asia/Tokyo", "токио": "Asia/Tokyo",
    "japan": "Asia/Tokyo", "япония": "Asia/Tokyo",
    "seoul": "Asia/Seoul", "сеул": "Asia/Seoul",
    "korea": "Asia/Seoul", "корея": "Asia/Seoul",
    "beijing": "Asia/Shanghai", "пекин": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai", "шанхай": "Asia/Shanghai",
    "china": "Asia/Shanghai", "китай": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong", "гонконг": "Asia/Hong_Kong",
    "singapore": "Asia/Singapore", "сингапур": "Asia/Singapore",
    "bangkok": "Asia/Bangkok", "бангкок": "Asia/Bangkok",
    "thailand": "Asia/Bangkok", "таиланд": "Asia/Bangkok",
    "phuket": "Asia/Bangkok", "пхукет": "Asia/Bangkok",
    "bali": "Asia/Makassar", "бали": "Asia/Makassar",
    "jakarta": "Asia/Jakarta", "джакарта": "Asia/Jakarta",
    "indonesia": "Asia/Jakarta", "индонезия": "Asia/Jakarta",
    "kuala lumpur": "Asia/Kuala_Lumpur", "куала-лумпур": "Asia/Kuala_Lumpur",
    "manila": "Asia/Manila", "манила": "Asia/Manila",
    "delhi": "Asia/Kolkata", "дели": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata", "мумбаи": "Asia/Kolkata",
    "india": "Asia/Kolkata", "индия": "Asia/Kolkata",
    "goa": "Asia/Kolkata", "гоа": "Asia/Kolkata",
    "sydney": "Australia/Sydney", "сидней": "Australia/Sydney",
    "australia": "Australia/Sydney", "австралия": "Australia/Sydney",
    "melbourne": "Australia/Melbourne", "мельбурн": "Australia/Melbourne",
    "auckland": "Pacific/Auckland", "окленд": "Pacific/Auckland",
    # Special
    "utc": "UTC", "gmt": "UTC",
}

_WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг",
                "пятница", "суббота", "воскресенье")


def resolve_timezone(location: str) -> Optional[ZoneInfo]:
    """Map a city/country name (EN/RU) or IANA zone name to a ZoneInfo."""
    name = location.strip().lower().replace("ё", "е")
    if not name:
        fallback = DEFAULT_TIMEZONE or "local"
        if fallback == "local":
            return None  # caller uses system local time
        name = fallback.lower()

    zone_name = _CITY_TIMEZONES.get(name)
    if zone_name is None:
        # Try as a literal IANA name; the tz database is case-sensitive,
        # so normalize "asia/tokyo" -> "Asia/Tokyo" as a second attempt.
        raw = location.strip().replace(" ", "_")
        candidates = [raw]
        if "/" in raw:
            candidates.append("/".join(part.title() for part in raw.split("/")))
        else:
            candidates.append(raw.upper())  # UTC, GMT, EST...
        for candidate in candidates:
            try:
                return ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError, KeyError):
                continue
        return None
    try:
        return ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):  # pragma: no cover
        return None


def _now_in(location: str) -> tuple[Optional[datetime], str]:
    """Current time in the requested location, plus a zone label."""
    if not location.strip() and not DEFAULT_TIMEZONE:
        now = datetime.now().astimezone()
        return now, str(now.tzinfo)
    zone = resolve_timezone(location)
    if zone is None:
        return None, location
    return datetime.now(zone), str(zone)


def _unknown_location_message(location: str) -> str:
    return (
        f"Unknown location: '{location}'. Use a major city or country name "
        "(e.g. 'Moscow', 'Стамбул', 'New York') or an IANA timezone like "
        "'Europe/Moscow'."
    )


def current_time(location: str = "") -> str:
    """Current time/date/weekday/timezone for a city, country, or IANA zone."""
    now, zone_label = _now_in(location)
    if now is None:
        return _unknown_location_message(location)
    offset = now.strftime("%z")
    offset_formatted = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    weekday_ru = _WEEKDAYS_RU[now.weekday()]
    return (
        f"Time: {now.strftime('%H:%M:%S')}\n"
        f"Date: {now.strftime('%Y-%m-%d')}\n"
        f"Weekday: {now.strftime('%A')} ({weekday_ru})\n"
        f"Timezone: {zone_label} ({offset_formatted})"
    )


def current_date(location: str = "") -> str:
    """Current date with weekday, week number, and day of year."""
    now, zone_label = _now_in(location)
    if now is None:
        return _unknown_location_message(location)
    weekday_ru = _WEEKDAYS_RU[now.weekday()]
    return (
        f"Date: {now.strftime('%Y-%m-%d')}\n"
        f"Weekday: {now.strftime('%A')} ({weekday_ru})\n"
        f"Month: {now.strftime('%B')}\n"
        f"Week of year: {now.isocalendar().week}\n"
        f"Day of year: {now.timetuple().tm_yday}\n"
        f"Timezone: {zone_label}"
    )


# --------------------------------------------------------------------------
# Unit conversion
# --------------------------------------------------------------------------

# category -> unit -> factor to the category's base unit.
_UNIT_TABLES: dict[str, dict[str, float]] = {
    "mass": {  # base: kilogram
        "kg": 1.0, "g": 0.001, "mg": 1e-6, "t": 1000.0,
        "lb": 0.45359237, "oz": 0.028349523125, "st": 6.35029318,
    },
    "length": {  # base: meter
        "m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001,
        "mi": 1609.344, "yd": 0.9144, "ft": 0.3048, "in": 0.0254,
        "nmi": 1852.0,
    },
    "volume": {  # base: liter
        "l": 1.0, "ml": 0.001, "m3": 1000.0,
        "gal": 3.785411784, "qt": 0.946352946, "pt": 0.473176473,
        "cup": 0.2365882365, "floz": 0.0295735295625, "bbl": 158.987294928,
    },
    "area": {  # base: square meter
        "m2": 1.0, "km2": 1e6, "cm2": 1e-4, "ha": 10000.0,
        "acre": 4046.8564224, "ft2": 0.09290304, "mi2": 2589988.110336,
    },
    "speed": {  # base: meter/second
        "ms": 1.0, "kmh": 1 / 3.6, "mph": 0.44704, "knot": 0.514444,
        "fts": 0.3048,
    },
    "data": {  # base: byte (binary multiples)
        "b": 1.0, "kb": 1024.0, "mb": 1024.0 ** 2, "gb": 1024.0 ** 3,
        "tb": 1024.0 ** 4, "bit": 0.125,
    },
    "time": {  # base: second
        "s": 1.0, "min": 60.0, "h": 3600.0, "day": 86400.0,
        "week": 604800.0, "month": 2629800.0, "year": 31557600.0,
    },
}

# alias -> (category, canonical unit); EN and RU spellings.
_UNIT_ALIASES: dict[str, tuple[str, str]] = {}


def _register_aliases(category: str, unit: str, *aliases: str) -> None:
    for alias in (unit, *aliases):
        _UNIT_ALIASES[alias] = (category, unit)


_register_aliases("mass", "kg", "kilogram", "kilograms", "кг", "килограмм", "килограммы", "кило")
_register_aliases("mass", "g", "gram", "grams", "г", "грамм", "граммы")
_register_aliases("mass", "mg", "milligram", "мг", "миллиграмм")
_register_aliases("mass", "t", "ton", "tonne", "tons", "т", "тонна", "тонны")
_register_aliases("mass", "lb", "lbs", "pound", "pounds", "фунт", "фунты", "фунтов")
_register_aliases("mass", "oz", "ounce", "ounces", "унция", "унции", "унций")
_register_aliases("mass", "st", "stone", "стоун")
_register_aliases("length", "m", "meter", "meters", "metre", "м", "метр", "метры", "метров")
_register_aliases("length", "km", "kilometer", "kilometers", "км", "километр", "километры", "километров")
_register_aliases("length", "cm", "centimeter", "см", "сантиметр", "сантиметры", "сантиметров")
_register_aliases("length", "mm", "millimeter", "мм", "миллиметр")
_register_aliases("length", "mi", "mile", "miles", "миля", "мили", "миль")
_register_aliases("length", "yd", "yard", "yards", "ярд", "ярды", "ярдов")
_register_aliases("length", "ft", "foot", "feet", "фут", "футы", "футов")
_register_aliases("length", "in", "inch", "inches", "дюйм", "дюймы", "дюймов")
_register_aliases("length", "nmi", "nautical mile", "морская миля")
_register_aliases("volume", "l", "liter", "liters", "litre", "л", "литр", "литры", "литров")
_register_aliases("volume", "ml", "milliliter", "мл", "миллилитр")
_register_aliases("volume", "m3", "cubic meter", "м3", "кубометр", "кубометры")
_register_aliases("volume", "gal", "gallon", "gallons", "галлон", "галлоны", "галлонов")
_register_aliases("volume", "qt", "quart", "кварта")
_register_aliases("volume", "pt", "pint", "пинта")
_register_aliases("volume", "cup", "cups", "чашка", "стакан")
_register_aliases("volume", "floz", "fl oz", "fluid ounce")
_register_aliases("volume", "bbl", "barrel", "баррель")
_register_aliases("area", "m2", "square meter", "кв м", "кв. м", "квадратный метр")
_register_aliases("area", "km2", "square kilometer", "кв км", "квадратный километр")
_register_aliases("area", "ha", "hectare", "га", "гектар", "гектары")
_register_aliases("area", "acre", "acres", "акр", "акры")
_register_aliases("area", "ft2", "square feet", "кв фут")
_register_aliases("area", "mi2", "square mile", "кв миля")
_register_aliases("speed", "ms", "m/s", "мс", "м/с", "метров в секунду")
_register_aliases("speed", "kmh", "km/h", "kph", "кмч", "км/ч", "километров в час")
_register_aliases("speed", "mph", "миль в час")
_register_aliases("speed", "knot", "knots", "kn", "узел", "узлы", "узлов")
_register_aliases("speed", "fts", "ft/s", "фут/с")
_register_aliases("data", "b", "byte", "bytes", "байт", "байты", "байтов")
_register_aliases("data", "kb", "kilobyte", "кб", "килобайт")
_register_aliases("data", "mb", "megabyte", "мб", "мегабайт")
_register_aliases("data", "gb", "gigabyte", "гб", "гигабайт")
_register_aliases("data", "tb", "terabyte", "тб", "терабайт")
_register_aliases("data", "bit", "бит")
_register_aliases("time", "s", "sec", "second", "seconds", "с", "сек", "секунда", "секунды", "секунд")
_register_aliases("time", "min", "minute", "minutes", "мин", "минута", "минуты", "минут")
_register_aliases("time", "h", "hr", "hour", "hours", "ч", "час", "часа", "часов")
_register_aliases("time", "day", "days", "d", "день", "дня", "дней", "сутки")
_register_aliases("time", "week", "weeks", "неделя", "недели", "недель")
_register_aliases("time", "month", "months", "месяц", "месяца", "месяцев")
_register_aliases("time", "year", "years", "год", "года", "лет")

_TEMPERATURE_ALIASES: dict[str, str] = {
    "c": "C", "°c": "C", "celsius": "C", "цельсий": "C", "цельсия": "C",
    "f": "F", "°f": "F", "fahrenheit": "F", "фаренгейт": "F", "фаренгейта": "F",
    "k": "K", "°k": "K", "kelvin": "K", "кельвин": "K", "кельвина": "K",
}


def _normalize_unit(unit: str) -> str:
    return unit.strip().lower().replace("ё", "е").rstrip(".")


def _to_celsius(value: float, scale: str) -> float:
    if scale == "C":
        return value
    if scale == "F":
        return (value - 32.0) * 5.0 / 9.0
    return value - 273.15  # K


def _from_celsius(value: float, scale: str) -> float:
    if scale == "C":
        return value
    if scale == "F":
        return value * 9.0 / 5.0 + 32.0
    return value + 273.15  # K


def _format_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    formatted = f"{value:.6g}"
    return formatted


def unit_converter(value: float, from_unit: str, to_unit: str) -> str:
    """Convert between units of mass, length, volume, area, speed, data,
    time, and temperature. Accepts English and Russian unit names."""
    src, dst = _normalize_unit(from_unit), _normalize_unit(to_unit)

    src_temp, dst_temp = _TEMPERATURE_ALIASES.get(src), _TEMPERATURE_ALIASES.get(dst)
    if src_temp and dst_temp:
        result = _from_celsius(_to_celsius(float(value), src_temp), dst_temp)
        return f"{_format_number(value)} °{src_temp} = {_format_number(round(result, 4))} °{dst_temp}"

    src_entry, dst_entry = _UNIT_ALIASES.get(src), _UNIT_ALIASES.get(dst)
    if src_entry is None or dst_entry is None:
        unknown = from_unit if src_entry is None and not src_temp else to_unit
        categories = ", ".join(sorted(_UNIT_TABLES))
        return (
            f"Unknown unit: '{unknown}'. Supported categories: {categories}, "
            "temperature (C/F/K). Units accept English and Russian names "
            "(kg/кг, mi/мили, l/литры...)."
        )
    src_cat, src_unit = src_entry
    dst_cat, dst_unit = dst_entry
    if src_cat != dst_cat:
        return (
            f"Cannot convert {src_cat} ('{from_unit}') to {dst_cat} "
            f"('{to_unit}') - the units measure different things."
        )
    base = float(value) * _UNIT_TABLES[src_cat][src_unit]
    result = base / _UNIT_TABLES[dst_cat][dst_unit]
    return f"{_format_number(value)} {src_unit} = {_format_number(result)} {dst_unit}"


# --------------------------------------------------------------------------
# Safe calculator
# --------------------------------------------------------------------------

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: None,  # handled specially with magnitude guards
}
_ALLOWED_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}

_MATH_FUNCTIONS: dict[str, object] = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "exp": math.exp, "log": math.log, "log2": math.log2, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "degrees": math.degrees, "radians": math.radians,
    "floor": math.floor, "ceil": math.ceil, "trunc": math.trunc,
    "factorial": math.factorial, "gcd": math.gcd, "mod": operator.mod,
    "hypot": math.hypot,
}
_MATH_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau}

_MAX_POW_EXPONENT = 1000.0
_MAX_FACTORIAL_ARG = 5000


def _safe_pow(base: float, exponent: float) -> float:
    if abs(exponent) > _MAX_POW_EXPONENT:
        raise ValueError(f"exponent too large (|exp| <= {_MAX_POW_EXPONENT:g})")
    if isinstance(base, int) and isinstance(exponent, int) and exponent > 0:
        if base != 0 and exponent * math.log10(abs(base) + 1) > 10000:
            raise ValueError("result would be astronomically large")
    return base ** exponent


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"unsupported constant: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_BINOPS:
            raise ValueError(f"operator not allowed: {op_type.__name__}")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if op_type is ast.Pow:
            return _safe_pow(left, right)
        return _ALLOWED_BINOPS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_UNARY:
            raise ValueError(f"operator not allowed: {op_type.__name__}")
        return _ALLOWED_UNARY[op_type](_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise ValueError("only plain math functions are allowed")
        func = _MATH_FUNCTIONS.get(node.func.id)
        if func is None:
            raise ValueError(f"unknown function: {node.func.id}")
        args = [_eval_node(arg) for arg in node.args]
        if node.func.id == "factorial":
            if len(args) != 1 or args[0] != int(args[0]) or not 0 <= args[0] <= _MAX_FACTORIAL_ARG:
                raise ValueError(f"factorial needs an integer 0..{_MAX_FACTORIAL_ARG}")
            args = [int(args[0])]
        return func(*args)  # type: ignore[operator]
    if isinstance(node, ast.Name):
        if node.id in _MATH_CONSTANTS:
            return _MATH_CONSTANTS[node.id]
        raise ValueError(f"unknown name: {node.id}")
    raise ValueError(f"unsupported expression element: {type(node).__name__}")


_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def calculator(expression: str) -> str:
    """Safely evaluate a math expression (arithmetic, percentages via N%,
    powers with ^ or **, roots, trigonometry, logarithms, constants pi/e)."""
    raw = expression.strip()
    if not raw:
        return "Error: expression must not be empty."
    if len(raw) > 500:
        return "Error: expression too long (max 500 characters)."

    prepared = raw.replace("^", "**").replace("×", "*").replace("÷", "/").replace("−", "-")
    prepared = _PERCENT_RE.sub(r"(\1/100)", prepared)

    try:
        tree = ast.parse(prepared, mode="eval")
        result = _eval_node(tree)
    except ZeroDivisionError:
        return "Error: division by zero."
    except (ValueError, TypeError, SyntaxError, OverflowError) as exc:
        return f"Error: {exc}"
    except RecursionError:
        return "Error: expression too deeply nested."

    if isinstance(result, float):
        if math.isnan(result):
            return "Error: result is not a number."
        if math.isinf(result):
            return "Error: result is infinite."
        if result == int(result) and abs(result) < 1e15:
            result = int(result)
    text = str(result)
    if len(text) > 200:
        text = f"{float(result):.12e}"
    return f"{raw} = {text}"


# --------------------------------------------------------------------------
# UUID and randomness
# --------------------------------------------------------------------------

def generate_uuid(count: int = 1) -> str:
    """Generate 1-50 random UUIDv4 values, one per line."""
    count = max(1, min(int(count), 50))
    return "\n".join(str(uuid_module.uuid4()) for _ in range(count))


_DICE_RE = re.compile(r"^(\d{1,3})d(\d{1,4})$", re.IGNORECASE)


def random_generator(
    kind: str = "number",
    minimum: float = 1,
    maximum: float = 100,
    length: int = 12,
    options: str = "",
) -> str:
    """Generate random values: integers, floats, strings, passwords,
    coin flips, dice rolls, or a choice from a comma-separated list."""
    kind = kind.strip().lower()

    if kind in ("number", "int", "integer", "число"):
        low, high = int(min(minimum, maximum)), int(max(minimum, maximum))
        return str(secrets.choice(range(low, high + 1)))

    if kind in ("float", "real", "дробное"):
        low, high = min(minimum, maximum), max(minimum, maximum)
        span = high - low
        value = low + (secrets.randbelow(10**9) / 10**9) * span
        return f"{value:.6f}"

    if kind in ("string", "строка"):
        length = max(1, min(int(length), 256))
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    if kind in ("password", "пароль"):
        length = max(8, min(int(length), 128))
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
        while True:
            candidate = "".join(secrets.choice(alphabet) for _ in range(length))
            if (any(c.islower() for c in candidate) and any(c.isupper() for c in candidate)
                    and any(c.isdigit() for c in candidate)):
                return candidate

    if kind in ("coin", "монета", "монетка"):
        return secrets.choice(("heads (орёл)", "tails (решка)"))

    if kind in ("dice", "кубик", "кости"):
        spec = options.strip() or "1d6"
        match = _DICE_RE.match(spec)
        if not match:
            return "Error: dice format is NdM, e.g. '2d6' (options parameter)."
        n, sides = int(match.group(1)), int(match.group(2))
        if not (1 <= n <= 100 and 2 <= sides <= 1000):
            return "Error: dice range is 1-100 dice with 2-1000 sides."
        rolls = [secrets.randbelow(sides) + 1 for _ in range(n)]
        return f"{spec}: rolls={rolls} total={sum(rolls)}"

    if kind in ("choice", "choose", "выбор"):
        items = [item.strip() for item in options.split(",") if item.strip()]
        if not items:
            return "Error: provide comma-separated options for kind='choice'."
        return secrets.choice(items)

    return (
        f"Unknown kind: '{kind}'. Supported: number, float, string, "
        "password, coin, dice (options='2d6'), choice (options='a,b,c')."
    )
