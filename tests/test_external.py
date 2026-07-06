"""external_tools: weather, currency, wikipedia (mocked HTTP)."""

import pytest

import external_tools


@pytest.fixture(autouse=True)
def fresh_cache():
    from cache import cache

    cache.clear()
    yield
    cache.clear()


GEO_RESPONSE = {"results": [{"name": "Moscow", "country": "Russia",
                             "latitude": 55.75, "longitude": 37.62}]}
FORECAST_RESPONSE = {
    "current": {"temperature_2m": 21.4, "apparent_temperature": 20.1,
                "relative_humidity_2m": 55, "weather_code": 2,
                "wind_speed_10m": 3.2, "wind_direction_10m": 180,
                "wind_gusts_10m": 6.1, "precipitation": 0},
    "daily": {"time": ["2026-07-06", "2026-07-07", "2026-07-08"],
              "weather_code": [2, 61, 3],
              "temperature_2m_max": [24.0, 19.5, 22.1],
              "temperature_2m_min": [15.2, 13.8, 14.0],
              "precipitation_probability_max": [10, 80, 30]},
}


class TestWeather:
    def test_happy_path(self, monkeypatch):
        responses = iter([GEO_RESPONSE, FORECAST_RESPONSE])
        monkeypatch.setattr(external_tools, "get_json", lambda *a, **k: next(responses))
        out = external_tools.weather("Moscow")
        assert "Moscow, Russia" in out
        assert "21.4°C" in out
        assert "feels like 20.1°C" in out
        assert "Humidity: 55%" in out
        assert "partly cloudy" in out
        assert "2026-07-07: 13.8..19.5°C" in out
        assert "S" in out  # wind direction (180° = S)

    def test_unknown_location(self, monkeypatch):
        monkeypatch.setattr(external_tools, "get_json", lambda *a, **k: {"results": []})
        out = external_tools.weather("Атлантида-Сити")
        assert "could not find location" in out

    def test_forecast_service_down(self, monkeypatch):
        responses = iter([GEO_RESPONSE, None])
        monkeypatch.setattr(external_tools, "get_json", lambda *a, **k: next(responses))
        out = external_tools.weather("Moscow")
        assert "unavailable" in out

    def test_empty_location(self):
        assert "Error" in external_tools.weather("  ")


ERAPI_RESPONSE = {"result": "success",
                  "time_last_update_utc": "Mon, 06 Jul 2026 00:02:31 +0000",
                  "rates": {"USD": 1.0, "EUR": 0.92, "RUB": 89.5, "TRY": 33.1}}


class TestCurrency:
    def test_usd_to_eur(self, monkeypatch):
        monkeypatch.setattr(external_tools, "get_json", lambda *a, **k: ERAPI_RESPONSE)
        out = external_tools.currency_rate("USD", "EUR", 100)
        assert "100 USD = 92" in out
        assert "1 USD = 0.92 EUR" in out

    def test_russian_aliases(self, monkeypatch):
        monkeypatch.setattr(external_tools, "get_json", lambda *a, **k: ERAPI_RESPONSE)
        out = external_tools.currency_rate("доллар", "рубль", 1)
        assert "USD" in out and "RUB" in out

    def test_symbol_aliases(self, monkeypatch):
        monkeypatch.setattr(external_tools, "get_json", lambda *a, **k: ERAPI_RESPONSE)
        out = external_tools.currency_rate("$", "₺")
        assert "TRY" in out

    def test_fallback_to_frankfurter(self, monkeypatch):
        frankfurter = {"date": "2026-07-04", "rates": {"EUR": 0.93}}
        responses = iter([None, frankfurter])  # er-api fails, frankfurter works
        monkeypatch.setattr(external_tools, "get_json", lambda *a, **k: next(responses))
        out = external_tools.currency_rate("USD", "EUR")
        assert "frankfurter" in out
        assert "0.93" in out

    def test_both_sources_down(self, monkeypatch):
        monkeypatch.setattr(external_tools, "get_json", lambda *a, **k: None)
        out = external_tools.currency_rate("USD", "EUR")
        assert "unavailable" in out

    def test_unknown_target_currency(self, monkeypatch):
        monkeypatch.setattr(external_tools, "get_json", lambda *a, **k: ERAPI_RESPONSE)
        out = external_tools.currency_rate("USD", "XYZ")
        assert "no rate" in out

    def test_invalid_code(self):
        assert "Error" in external_tools.currency_rate("не деньги вовсе", "EUR")


WIKI_SEARCH_RESPONSE = {"query": {"search": [{"title": "Python (programming language)"},
                                             {"title": "Monty Python"}]}}
WIKI_SUMMARY_RESPONSE = {
    "title": "Python (programming language)",
    "description": "High-level programming language",
    "extract": "Python is a high-level, general-purpose programming language.",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Python_(programming_language)"}},
}


class TestWikipedia:
    def test_happy_path(self, monkeypatch):
        responses = iter([WIKI_SEARCH_RESPONSE, WIKI_SUMMARY_RESPONSE])
        monkeypatch.setattr(external_tools, "get_json", lambda *a, **k: next(responses))
        out = external_tools.wikipedia_search("python language")
        assert "Python (programming language)" in out
        assert "high-level" in out
        assert "URL: https://en.wikipedia.org/wiki/Python" in out
        assert "Monty Python" in out  # other matches listed

    def test_no_article(self, monkeypatch):
        monkeypatch.setattr(external_tools, "get_json",
                            lambda *a, **k: {"query": {"search": []}})
        out = external_tools.wikipedia_search("абракадабра-несуществующее-1234567")
        assert "No Wikipedia article" in out

    def test_language_detection_targets_ru(self, monkeypatch):
        urls = []

        def track(url, **kwargs):
            urls.append(url)
            return None

        monkeypatch.setattr(external_tools, "get_json", track)
        external_tools.wikipedia_search("Пушкин биография")
        assert any("ru.wikipedia.org" in u for u in urls)

    def test_empty_query(self):
        assert "Error" in external_tools.wikipedia_search("")
