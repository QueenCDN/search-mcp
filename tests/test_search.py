"""search orchestration: formatting, limits, caching, degradation."""

import pytest

import search as search_module
from providers import SearchHit
from utils import MAX_TOTAL_CHARS


@pytest.fixture(autouse=True)
def fresh_cache():
    """Each test starts with an empty cache."""
    from cache import cache

    cache.clear()
    yield
    cache.clear()


def _hits(n: int, prefix: str = "site") -> list[SearchHit]:
    return [
        SearchHit(url=f"https://{prefix}{i}.com/article", title=f"Title {i}",
                  snippet=f"Snippet {i}")
        for i in range(n)
    ]


class TestSearchWeb:
    def test_happy_path(self, monkeypatch):
        monkeypatch.setattr(search_module, "web_search", lambda q, r, n: _hits(3))
        monkeypatch.setattr(search_module, "fetch_and_extract",
                            lambda url, timeout=8: f"Content of {url} " * 20)
        out = search_module.search_web("test query")
        assert "[1] Title 0" in out
        assert "URL: https://site0.com/article" in out
        assert "Content of" in out

    def test_empty_query(self):
        assert "Error" in search_module.search_web("   ")

    def test_no_results(self, monkeypatch):
        monkeypatch.setattr(search_module, "web_search", lambda q, r, n: [])
        out = search_module.search_web("gibberish query")
        assert "No search results" in out

    def test_failed_pages_fall_back_to_snippets(self, monkeypatch):
        monkeypatch.setattr(search_module, "web_search", lambda q, r, n: _hits(3))
        monkeypatch.setattr(search_module, "fetch_and_extract", lambda url, timeout=8: None)
        out = search_module.search_web("all pages fail")
        assert "Snippet 0" in out  # degraded but useful

    def test_provider_exception_degrades(self, monkeypatch):
        def boom(q, r, n):
            raise RuntimeError("network down")

        monkeypatch.setattr(search_module, "web_search", boom)
        out = search_module.search_web("query")
        assert "Search failed" in out

    def test_total_output_bounded(self, monkeypatch):
        monkeypatch.setattr(search_module, "web_search", lambda q, r, n: _hits(5))
        monkeypatch.setattr(search_module, "fetch_and_extract",
                            lambda url, timeout=8: "word " * 5000)
        out = search_module.search_web("big output")
        assert len(out) <= MAX_TOTAL_CHARS + 100

    def test_cache_hit_skips_recompute(self, monkeypatch):
        calls = []

        def counted(q, r, n):
            calls.append(1)
            return _hits(1)

        monkeypatch.setattr(search_module, "web_search", counted)
        monkeypatch.setattr(search_module, "fetch_and_extract",
                            lambda url, timeout=8: "Some content " * 30)
        search_module.search_web("repeat me")
        search_module.search_web("repeat me")
        assert len(calls) == 1

    def test_no_results_not_cached(self, monkeypatch):
        outcomes = iter([[], _hits(1)])
        monkeypatch.setattr(search_module, "web_search",
                            lambda q, r, n: next(outcomes))
        monkeypatch.setattr(search_module, "fetch_and_extract",
                            lambda url, timeout=8: "Recovered content " * 30)
        first = search_module.search_web("flaky")
        second = search_module.search_web("flaky")
        assert "No search results" in first
        assert "Recovered content" in second  # error was not cached


class TestSearchNews:
    def test_news_formatting_includes_source_and_date(self, monkeypatch):
        hits = [SearchHit(url="https://reuters.com/a", title="Story",
                          snippet="Snip", source="Reuters", date="2026-07-06 10:00")]
        monkeypatch.setattr(search_module, "news_search", lambda q, r, n: hits)
        monkeypatch.setattr(search_module, "fetch_and_extract",
                            lambda url, timeout=8: "Article body " * 30)
        out = search_module.search_news("openai")
        assert "Reuters" in out
        assert "2026-07-06" in out

    def test_noscrape_hits_use_snippets_without_fetching(self, monkeypatch):
        hits = [SearchHit(url="https://news.google.com/x", title="Headline",
                          snippet="Google snippet", source="FT", date="2026-07-06",
                          no_scrape=True)]
        fetched = []

        def track_fetch(url, timeout=8):
            fetched.append(url)
            return "should not be called"

        monkeypatch.setattr(search_module, "news_search", lambda q, r, n: hits)
        monkeypatch.setattr(search_module, "fetch_and_extract", track_fetch)
        out = search_module.search_news("query")
        assert fetched == []  # no_scrape page was never downloaded
        assert "Google snippet" in out


class TestFetchSinglePage:
    def test_happy_path(self, monkeypatch):
        monkeypatch.setattr(search_module, "fetch_and_extract",
                            lambda url, timeout=8: "Page body here")
        out = search_module.fetch_single_page("https://example.com/a")
        assert out == "Page body here"

    def test_empty_url(self):
        assert "Error" in search_module.fetch_single_page("")

    def test_unreadable_page(self, monkeypatch):
        monkeypatch.setattr(search_module, "fetch_and_extract", lambda url, timeout=8: None)
        out = search_module.fetch_single_page("https://example.com/broken")
        assert "Could not extract" in out
