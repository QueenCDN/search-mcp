"""providers: URL normalization, ranking, junk filtering, RSS parsing."""

from providers import (
    SearchHit,
    _news_bing_rss,
    _rss_items,
    _search_ddg_html,
    _unwrap_ddg_redirect,
    is_blocked,
    is_noscrape,
    normalize_url,
    rank_hits,
    trust_boost,
)


class TestNormalizeUrl:
    def test_strips_tracking_params(self):
        url = "https://example.com/page?utm_source=x&id=5&fbclid=abc"
        assert normalize_url(url) == "https://example.com/page?id=5"

    def test_strips_fragment_and_trailing_slash(self):
        assert normalize_url("https://Example.com/Page/#section") == "https://example.com/Page"

    def test_equivalent_urls_collapse(self):
        a = normalize_url("https://www.example.com/a/?utm_campaign=z")
        b = normalize_url("https://www.example.com/a")
        assert a == b


class TestDomainQuality:
    def test_trusted_exact(self):
        assert trust_boost("https://www.reuters.com/tech/article") == 1.0

    def test_trusted_subdomain(self):
        assert trust_boost("https://blogs.reuters.com/x") == 1.0

    def test_gov_boost(self):
        assert trust_boost("https://www.usa.gov/taxes") > 0

    def test_unknown_no_boost(self):
        assert trust_boost("https://random-seo-blog.biz/page") == 0.0

    def test_blocked_social(self):
        assert is_blocked("https://www.pinterest.com/pin/1")
        assert is_blocked("https://reddit.com/r/x")
        assert not is_blocked("https://reuters.com/article")

    def test_noscrape(self):
        assert is_noscrape("https://www.msn.com/en-us/news/x")
        assert is_noscrape("https://news.google.com/rss/articles/abc")
        assert not is_noscrape("https://en.wikipedia.org/wiki/X")


class TestRankHits:
    def test_trusted_outranks_position(self):
        hits = [
            SearchHit(url="https://random-blog.biz/a", title="Blog"),
            SearchHit(url="https://www.reuters.com/a", title="Reuters"),
        ]
        ranked = rank_hits(hits, 5)
        assert "reuters" in ranked[0].url

    def test_blocked_dropped(self):
        hits = [
            SearchHit(url="https://pinterest.com/pin/1", title="Pin"),
            SearchHit(url="https://example.com/a", title="A"),
        ]
        ranked = rank_hits(hits, 5)
        assert len(ranked) == 1
        assert "example.com" in ranked[0].url

    def test_duplicate_urls_merged_with_bonus(self):
        hits = [
            SearchHit(url="https://example.com/a?utm_source=ddg", title="A", snippet=""),
            SearchHit(url="https://site-b.com/x", title="B"),
            SearchHit(url="https://example.com/a", title="A again", snippet="snippet here"),
        ]
        ranked = rank_hits(hits, 5)
        example = [h for h in ranked if "example.com" in h.url]
        assert len(example) == 1
        assert example[0].snippet == "snippet here"  # merged from duplicate
        assert ranked[0] is example[0]  # corroboration bonus wins

    def test_domain_cap(self):
        hits = [SearchHit(url=f"https://same.com/{i}", title=str(i)) for i in range(5)]
        hits.append(SearchHit(url="https://other.com/x", title="other"))
        ranked = rank_hits(hits, 10, per_domain_cap=2)
        same_count = sum(1 for h in ranked if "same.com" in h.url)
        assert same_count == 2

    def test_max_results_respected(self):
        hits = [SearchHit(url=f"https://site{i}.com/", title=str(i)) for i in range(20)]
        assert len(rank_hits(hits, 5)) == 5

    def test_invalid_urls_dropped(self):
        hits = [SearchHit(url="not-a-url", title="bad"),
                SearchHit(url="https://ok.com/", title="ok")]
        ranked = rank_hits(hits, 5)
        assert len(ranked) == 1


class TestDdgRedirect:
    def test_unwraps_uddg(self):
        wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=abc"
        assert _unwrap_ddg_redirect(wrapped) == "https://example.com/page"

    def test_plain_url_unchanged(self):
        assert _unwrap_ddg_redirect("https://example.com/") == "https://example.com/"

    def test_protocol_relative(self):
        assert _unwrap_ddg_redirect("//example.com/x") == "https://example.com/x"


class TestRssParsing:
    def test_rss_items_recover_from_bad_xml(self):
        xml = "<rss><channel><item><title>T</title><link>https://a.com</link></item>"
        items = _rss_items(xml)  # unclosed tags: recover mode should cope
        assert len(items) == 1

    def test_rss_items_garbage(self):
        assert _rss_items("complete garbage, not xml at all") == []


class TestProviderParsing:
    """Parsers against synthetic fixtures shaped like the real responses."""

    def test_ddg_html_parser(self, monkeypatch):
        page = """
        <html><body>
          <div class="result results_links results_links_deep web-result">
           <div class="links_main links_deep result__body">
            <h2 class="result__title">
              <a rel="nofollow" class="result__a" href="https://openai.com/news/">OpenAI News</a>
            </h2>
            <a class="result__snippet" href="https://openai.com/news/">Latest updates from OpenAI.</a>
           </div>
          </div>
        </body></html>
        """

        class FakeResult:
            ok = True
            text = page
            status = 200

        monkeypatch.setattr("providers.http_post", lambda *a, **k: FakeResult())
        from utils import resolve_region

        hits = _search_ddg_html("openai", resolve_region("openai"), 5)
        assert len(hits) == 1
        assert hits[0].url == "https://openai.com/news/"
        assert hits[0].title == "OpenAI News"
        assert "Latest updates" in hits[0].snippet

    def test_bing_news_parser_unwraps_apiclick(self, monkeypatch):
        xml = """<?xml version="1.0"?>
        <rss version="2.0" xmlns:News="https://www.bing.com:443/news/search?q=x&amp;format=rss">
          <channel><item>
            <title>Big story</title>
            <link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;aid=&amp;url=https%3A%2F%2Freuters.com%2Fstory&amp;c=x</link>
            <description>Something happened.</description>
            <pubDate>Mon, 06 Jul 2026 04:00:00 GMT</pubDate>
            <News:Source>Reuters</News:Source>
          </item></channel>
        </rss>"""

        class FakeResult:
            ok = True
            text = xml
            status = 200

        monkeypatch.setattr("providers.http_get", lambda *a, **k: FakeResult())
        from utils import resolve_region

        hits = _news_bing_rss("x", resolve_region("x"), 5)
        assert len(hits) == 1
        assert hits[0].url == "https://reuters.com/story"
        assert hits[0].source == "Reuters"
        assert hits[0].date.startswith("2026-07-06")
