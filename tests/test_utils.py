"""utils: language detection, region resolution, text cleaning, URLs."""

from utils import (
    clean_text,
    detect_language,
    domain_of,
    is_valid_http_url,
    resolve_region,
    truncate_text,
)


class TestDetectLanguage:
    def test_russian(self):
        assert detect_language("столица Франции") == "ru"

    def test_english_default(self):
        assert detect_language("capital of France") == "en"

    def test_german_stopwords(self):
        assert detect_language("was ist die Hauptstadt von Frankreich") == "de"

    def test_french_stopwords(self):
        assert detect_language("quelle est la capitale de la France pour les nuls") == "fr"

    def test_turkish_chars(self):
        assert detect_language("İstanbul'da hava durumu nasıl") == "tr"

    def test_japanese(self):
        assert detect_language("東京の天気 きょう") == "ja"

    def test_chinese(self):
        assert detect_language("北京天气") == "zh"

    def test_arabic(self):
        assert detect_language("ما هي عاصمة فرنسا") == "ar"

    def test_empty_defaults_english(self):
        assert detect_language("") == "en"

    def test_brand_names_stay_english(self):
        assert detect_language("OpenAI GPT-5 release date") == "en"


class TestResolveRegion:
    def test_russian_region(self):
        region = resolve_region("новости технологий")
        assert region.language == "ru"
        assert region.ddg_region == "ru-ru"
        assert region.bing_market == "ru-RU"
        assert region.country == "RU"

    def test_english_region(self):
        region = resolve_region("tech news")
        assert region.ddg_region == "us-en"


class TestCleanText:
    def test_drops_cookie_banner_lines(self):
        text = "Real article content here.\nAccept all cookies\nMore real content."
        cleaned = clean_text(text)
        assert "cookies" not in cleaned.lower()
        assert "Real article content" in cleaned

    def test_drops_russian_boilerplate(self):
        text = "Настоящий текст статьи.\nПодписаться на рассылку\nПродолжение статьи."
        cleaned = clean_text(text)
        assert "Подписаться" not in cleaned
        assert "Настоящий текст" in cleaned

    def test_deduplicates_short_repeated_lines(self):
        text = "Menu item\nParagraph one is long enough to keep.\nMenu item"
        cleaned = clean_text(text)
        assert cleaned.count("Menu item") == 1

    def test_collapses_blank_runs(self):
        cleaned = clean_text("One line.\n\n\n\n\nAnother line.")
        assert "\n\n\n" not in cleaned

    def test_drops_single_characters_and_punct(self):
        cleaned = clean_text("Useful sentence.\n|\n*\n-\nAnother useful sentence.")
        assert "|" not in cleaned

    def test_empty_input(self):
        assert clean_text("") == ""


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate_text("hello", 100) == "hello"

    def test_cuts_on_word_boundary(self):
        text = "word " * 100
        result = truncate_text(text, 50)
        assert len(result) <= 60
        assert result.endswith("[...]")


class TestUrls:
    def test_valid(self):
        assert is_valid_http_url("https://example.com/page")

    def test_invalid_scheme(self):
        assert not is_valid_http_url("ftp://example.com")
        assert not is_valid_http_url("javascript:alert(1)")

    def test_no_host(self):
        assert not is_valid_http_url("https://")
        assert not is_valid_http_url("not a url")

    def test_domain_of(self):
        assert domain_of("https://www.Example.COM:443/x?q=1") == "example.com"
        assert domain_of("https://sub.news.bbc.co.uk/a") == "sub.news.bbc.co.uk"
