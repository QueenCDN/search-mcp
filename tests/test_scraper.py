"""scraper: extraction quality, fallback behavior, failure handling."""

from scraper import extract_readable_text, fetch_and_extract

ARTICLE_HTML = """
<!DOCTYPE html>
<html><head><title>Test Article</title></head>
<body>
  <nav><a href="/">Home</a><a href="/about">About</a></nav>
  <div class="cookie-banner">We use cookies. Accept all cookies</div>
  <header><h1>Site header</h1></header>
  <main>
    <article>
      <h1>The Actual Article Title</h1>
      <p>{p1}</p>
      <p>{p2}</p>
      <p>{p3}</p>
    </article>
  </main>
  <aside class="sidebar">Related links and junk</aside>
  <div class="advert">Buy things now!</div>
  <footer>All rights reserved. © 2026</footer>
  <script>console.log("garbage");</script>
</body></html>
""".format(
    p1="This is the first meaningful paragraph of the article, long enough "
       "to be treated as real content by the extraction pipeline.",
    p2="The second paragraph continues the discussion with additional "
       "details and enough length to matter for extraction thresholds.",
    p3="A third paragraph closes out the article with concluding thoughts "
       "and a satisfying amount of prose for the extractor to keep.",
)


class TestExtraction:
    def test_keeps_article_text(self):
        text = extract_readable_text(ARTICLE_HTML, "https://example.com/a")
        assert text is not None
        assert "first meaningful paragraph" in text
        assert "second paragraph" in text

    def test_removes_boilerplate(self):
        text = extract_readable_text(ARTICLE_HTML, "https://example.com/a")
        assert "Accept all cookies" not in text
        assert "console.log" not in text
        assert "Buy things now" not in text

    def test_plain_text_passthrough(self):
        text = extract_readable_text("Just a plain text file.\nSecond line here.",
                                     "https://example.com/robots.txt")
        assert "plain text file" in text

    def test_empty_html(self):
        assert extract_readable_text("<html><body></body></html>",
                                     "https://example.com/") is None

    def test_bs4_fallback_on_minimal_page(self):
        # A page trafilatura tends to reject (no article markup, short).
        page = ("<html><body><div>" +
                "Short but real content sentence that should survive. " * 8 +
                "</div></body></html>")
        text = extract_readable_text(page, "https://example.com/x")
        assert text is not None
        assert "real content" in text


class TestFetchFailures:
    def test_invalid_url(self):
        assert fetch_and_extract("not-a-url") is None

    def test_http_error(self, monkeypatch):
        from net import FetchResult

        monkeypatch.setattr("scraper.http_get",
                            lambda *a, **k: FetchResult(ok=False, status=404, error="HTTP 404"))
        assert fetch_and_extract("https://example.com/missing") is None

    def test_bot_challenge_skipped(self, monkeypatch):
        from net import FetchResult

        page = ("<html><head><title>Just a moment...</title></head>"
                "<body><div id='cf_chl_opt'>Checking your browser before accessing"
                " example.com</div></body></html>")
        monkeypatch.setattr("scraper.http_get",
                            lambda *a, **k: FetchResult(ok=True, status=200, text=page,
                                                        content_type="text/html"))
        assert fetch_and_extract("https://example.com/protected") is None

    def test_ordinary_page_mentioning_captcha_not_blocked(self, monkeypatch):
        from net import FetchResult

        # Regression: Wikipedia's config JS contains the word "captcha";
        # that must not be treated as a challenge page.
        page = ("<html><head><title>CAPTCHA - Wikipedia</title>"
                "<script>RLCONF={\"wgCaptcha\":false}</script></head><body><main>"
                + "<p>A CAPTCHA is a type of challenge-response test used in "
                  "computing to determine whether the user is human. This "
                  "paragraph repeats to satisfy extraction thresholds. " * 5
                + "</p></main></body></html>")
        monkeypatch.setattr("scraper.http_get",
                            lambda *a, **k: FetchResult(ok=True, status=200, text=page,
                                                        content_type="text/html"))
        assert fetch_and_extract("https://en.wikipedia.org/wiki/CAPTCHA") is not None

    def test_non_html_skipped(self, monkeypatch):
        from net import FetchResult

        monkeypatch.setattr("scraper.http_get",
                            lambda *a, **k: FetchResult(ok=True, status=200, text="binarydata",
                                                        content_type="application/pdf"))
        assert fetch_and_extract("https://example.com/doc.pdf") is None
