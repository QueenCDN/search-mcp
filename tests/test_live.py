"""Live end-to-end tests hitting real external services.

Excluded by default (see pytest.ini). Run explicitly with:

    pytest -m live -v
"""

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def fresh_cache():
    from cache import cache

    cache.clear()
    yield


class TestLiveSearch:
    def test_web_search_english(self):
        from search import search_web

        out = search_web("what is the capital of France")
        assert "URL:" in out
        assert len(out) > 500

    def test_web_search_russian(self):
        from search import search_web

        out = search_web("столица Франции")
        assert "URL:" in out

    def test_news_search(self):
        from search import search_news

        out = search_news("artificial intelligence")
        assert "URL:" in out

    def test_fetch_page(self):
        from search import fetch_single_page

        out = fetch_single_page("https://en.wikipedia.org/wiki/Paris")
        assert "Paris" in out
        assert len(out) > 1000


class TestLiveApis:
    def test_weather(self):
        from external_tools import weather

        out = weather("Moscow")
        assert "Weather in Moscow" in out
        assert "°C" in out

    def test_currency(self):
        from external_tools import currency_rate

        out = currency_rate("USD", "EUR", 100)
        assert "100 USD" in out
        assert "EUR" in out

    def test_currency_rub(self):
        from external_tools import currency_rate

        out = currency_rate("USD", "RUB")
        assert "RUB" in out

    def test_wikipedia_en(self):
        from external_tools import wikipedia_search

        out = wikipedia_search("Python programming language")
        assert "wikipedia.org" in out

    def test_wikipedia_ru(self):
        from external_tools import wikipedia_search

        out = wikipedia_search("Пушкин")
        assert "wikipedia.org" in out
