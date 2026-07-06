"""Search providers with cascading fallback, ranking, and source quality.

Replaces the ``duckduckgo-search`` package, which hardcodes unstable
scraping backends and is the root cause of persistent ``403 Ratelimit``
errors. Each provider here is a small, transparent client that:

* is rate-limited per backend (see :mod:`net`),
* is taken out of rotation by a circuit breaker after failures,
* falls back to the next backend in the cascade on any problem.

Web cascade:   DDG HTML -> DDG Lite -> Bing Web RSS
News cascade:  Bing News RSS -> Google News RSS (snippets) -> web cascade

Results are merged, de-duplicated, cleaned of junk domains, and re-ranked
so that authoritative sources (news wires, major outlets, documentation,
Wikipedia) come first.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import parse_qs, quote_plus, urlparse, urlunparse

from lxml import etree, html as lxml_html

from net import breaker, http_get, http_post
from utils import SEARCH_RETRY_ATTEMPTS, SearchRegion, domain_of, get_logger

logger = get_logger("providers")


@dataclass
class SearchHit:
    """A single search result before its page has been downloaded."""

    url: str
    title: str
    snippet: str = ""
    source: str = ""
    date: str = ""
    score: float = 0.0
    no_scrape: bool = field(default=False, repr=False)


# --------------------------------------------------------------------------
# Domain quality lists
# --------------------------------------------------------------------------

# Authoritative sources get a ranking boost. Matched by suffix, so
# "blogs.reuters.com" matches "reuters.com".
TRUSTED_DOMAINS: dict[str, float] = {
    # News wires and major outlets
    "reuters.com": 1.0, "apnews.com": 1.0, "bbc.com": 0.9, "bbc.co.uk": 0.9,
    "bloomberg.com": 0.8, "cnbc.com": 0.8, "ft.com": 0.8, "wsj.com": 0.7,
    "nytimes.com": 0.7, "theguardian.com": 0.8, "washingtonpost.com": 0.7,
    "economist.com": 0.7, "npr.org": 0.7, "axios.com": 0.6,
    # Tech press
    "techcrunch.com": 0.8, "theverge.com": 0.8, "arstechnica.com": 0.8,
    "wired.com": 0.7, "engadget.com": 0.6, "venturebeat.com": 0.5,
    "zdnet.com": 0.5, "tomshardware.com": 0.6, "9to5mac.com": 0.5,
    # Russian-language outlets
    "rbc.ru": 0.7, "kommersant.ru": 0.7, "vedomosti.ru": 0.7,
    "interfax.ru": 0.8, "tass.ru": 0.7, "habr.com": 0.7, "cnews.ru": 0.5,
    # Reference and documentation
    "wikipedia.org": 0.9, "britannica.com": 0.7, "arxiv.org": 0.8,
    "nature.com": 0.8, "sciencedirect.com": 0.6, "nih.gov": 0.9,
    "github.com": 0.8, "stackoverflow.com": 0.8,
    "docs.python.org": 0.9, "developer.mozilla.org": 0.9,
    "learn.microsoft.com": 0.8, "kubernetes.io": 0.7,
    # Primary sources for tech topics
    "openai.com": 0.8, "anthropic.com": 0.8, "deepmind.google": 0.7,
    "blog.google": 0.6, "microsoft.com": 0.5, "apple.com": 0.5,
    "meta.com": 0.5, "ollama.com": 0.7, "huggingface.co": 0.7,
}

# Never useful as search results (social feeds requiring JS/login, video,
# and Q&A sites that block scrapers and add no extractable text).
BLOCKED_DOMAINS: frozenset[str] = frozenset({
    "pinterest.com", "pinterest.ru", "facebook.com", "instagram.com",
    "x.com", "twitter.com", "tiktok.com", "linkedin.com", "threads.net",
    "youtube.com", "youtu.be", "vimeo.com",
    "reddit.com", "quora.com",
    "slideshare.net", "scribd.com", "coursehero.com", "brainly.com",
    "ask.com", "answers.com",
})

# Real content but unscrapable (JS shells / hard paywalls): keep the hit,
# rely on the search snippet instead of downloading the page.
NOSCRAPE_DOMAINS: frozenset[str] = frozenset({
    "msn.com", "news.google.com", "forbes.com", "wsj.com", "ft.com",
    "bloomberg.com", "medium.com",
})

_TRACKING_PARAMS_PREFIXES = ("utm_", "fbclid", "gclid", "yclid", "mc_cid", "mc_eid", "ref_")


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication and cache keys."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url
    if not parsed.netloc:
        return url
    query_pairs = [
        pair for pair in parsed.query.split("&")
        if pair and not pair.split("=", 1)[0].lower().startswith(_TRACKING_PARAMS_PREFIXES)
    ]
    return urlunparse((
        parsed.scheme.lower() or "https",
        parsed.netloc.lower(),
        parsed.path.rstrip("/") or "/",
        "",
        "&".join(query_pairs),
        "",
    ))


def trust_boost(url: str) -> float:
    """Ranking boost for authoritative domains (suffix-matched)."""
    domain = domain_of(url)
    if not domain:
        return 0.0
    for trusted, boost in TRUSTED_DOMAINS.items():
        if domain == trusted or domain.endswith("." + trusted):
            return boost
    if domain.endswith((".gov", ".edu")) or ".gov." in domain or ".edu." in domain:
        return 0.6
    return 0.0


def is_blocked(url: str) -> bool:
    domain = domain_of(url)
    return any(domain == b or domain.endswith("." + b) for b in BLOCKED_DOMAINS)


def is_noscrape(url: str) -> bool:
    domain = domain_of(url)
    return any(domain == b or domain.endswith("." + b) for b in NOSCRAPE_DOMAINS)


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

def _unwrap_ddg_redirect(href: str) -> str:
    """Resolve DDG's /l/?uddg=<url> redirect wrapper to the target URL."""
    if "duckduckgo.com/l/" in href or href.startswith("/l/"):
        try:
            target = parse_qs(urlparse(href).query).get("uddg", [""])[0]
            if target:
                return target
        except (ValueError, KeyError):
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


_DDG_BLOCK_MARKERS = ("anomaly-modal", "unfortunately, bots", "botlink", "challenge-form")


def _looks_blocked(page: str) -> bool:
    head = page[:6000].lower()
    return any(marker in head for marker in _DDG_BLOCK_MARKERS)


def _first_text(element, xpaths: tuple[str, ...]) -> str:
    for xp in xpaths:
        nodes = element.xpath(xp)
        if nodes:
            return " ".join(nodes[0].text_content().split())
    return ""


def _rss_items(xml_text: str) -> list[etree._Element]:
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(xml_text.encode("utf-8", "replace"), parser=parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        logger.warning("RSS parse failed: %s", exc)
        return []
    if root is None:
        return []
    return root.findall(".//item")


def _rss_child_text(item: etree._Element, localname: str) -> str:
    """Text of the first child whose local name matches (namespace-agnostic)."""
    for child in item:
        if isinstance(child.tag, str) and etree.QName(child).localname.lower() == localname.lower():
            return (child.text or "").strip()
    return ""


def _strip_html(text: str) -> str:
    if "<" not in text:
        return text
    try:
        return " ".join(lxml_html.fromstring(f"<div>{text}</div>").text_content().split())
    except (etree.ParserError, ValueError):
        return text


def _format_rss_date(raw: str) -> str:
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return raw[:25]


# --------------------------------------------------------------------------
# Web search providers
# --------------------------------------------------------------------------

def _search_ddg_html(query: str, region: SearchRegion, max_results: int) -> list[SearchHit]:
    result = http_post(
        "https://html.duckduckgo.com/html/",
        provider_key="ddg_html",
        data={"q": query, "kl": region.ddg_region, "b": ""},
        headers={"Referer": "https://html.duckduckgo.com/"},
    )
    if not result.ok:
        raise RuntimeError(f"ddg_html: {result.error or result.status}")
    if _looks_blocked(result.text):
        breaker.record_failure("ddg_html", blocked=True)
        raise RuntimeError("ddg_html: bot challenge page")

    doc = lxml_html.fromstring(result.text)
    hits: list[SearchHit] = []
    for anchor in doc.xpath("//a[contains(@class,'result__a')]"):
        href = _unwrap_ddg_redirect(anchor.get("href") or "")
        if not href.startswith("http"):
            continue
        # The snippet lives in the result body <div>, a level above the
        # anchor's <h2 class="result__title"> parent - so look for the
        # nearest *div* ancestor, not just any element with "result" in
        # its class (the h2 itself would match that).
        containers = anchor.xpath("ancestor::div[contains(@class,'result')][1]")
        snippet = _first_text(
            containers[0], ("./descendant::a[contains(@class,'result__snippet')]",)
        ) if containers else ""
        hits.append(SearchHit(url=href, title=" ".join(anchor.text_content().split()),
                              snippet=snippet))
        if len(hits) >= max_results * 3:
            break
    return hits


def _search_ddg_lite(query: str, region: SearchRegion, max_results: int) -> list[SearchHit]:
    result = http_post(
        "https://lite.duckduckgo.com/lite/",
        provider_key="ddg_lite",
        data={"q": query, "kl": region.ddg_region},
        headers={"Referer": "https://lite.duckduckgo.com/"},
    )
    if not result.ok:
        raise RuntimeError(f"ddg_lite: {result.error or result.status}")
    if _looks_blocked(result.text):
        breaker.record_failure("ddg_lite", blocked=True)
        raise RuntimeError("ddg_lite: bot challenge page")

    doc = lxml_html.fromstring(result.text)
    links = doc.xpath("//a[@class='result-link']")
    snippets = doc.xpath("//td[@class='result-snippet']")
    hits: list[SearchHit] = []
    for i, anchor in enumerate(links):
        href = _unwrap_ddg_redirect(anchor.get("href") or "")
        if not href.startswith("http"):
            continue
        snippet = " ".join(snippets[i].text_content().split()) if i < len(snippets) else ""
        hits.append(SearchHit(url=href, title=" ".join(anchor.text_content().split()),
                              snippet=snippet))
        if len(hits) >= max_results * 3:
            break
    return hits


def _search_bing_rss(query: str, region: SearchRegion, max_results: int) -> list[SearchHit]:
    result = http_get(
        "https://www.bing.com/search",
        provider_key="bing_rss",
        params={"q": query, "format": "rss", "count": str(max_results * 2),
                "mkt": region.bing_market},
    )
    if not result.ok:
        raise RuntimeError(f"bing_rss: {result.error or result.status}")

    hits: list[SearchHit] = []
    for item in _rss_items(result.text):
        link = _rss_child_text(item, "link")
        title = _rss_child_text(item, "title")
        if not link.startswith("http") or not title:
            continue
        hits.append(SearchHit(
            url=link,
            title=title,
            snippet=_strip_html(_rss_child_text(item, "description")),
        ))
    return hits


# --------------------------------------------------------------------------
# News search providers
# --------------------------------------------------------------------------

def _news_bing_rss(query: str, region: SearchRegion, max_results: int) -> list[SearchHit]:
    result = http_get(
        "https://www.bing.com/news/search",
        provider_key="bing_news",
        params={"q": query, "format": "rss", "mkt": region.bing_market,
                "count": str(max_results * 2)},
    )
    if not result.ok:
        raise RuntimeError(f"bing_news: {result.error or result.status}")

    hits: list[SearchHit] = []
    for item in _rss_items(result.text):
        link = _rss_child_text(item, "link")
        # Bing wraps article links in an apiclick redirect; the real URL
        # is carried in its "url" query parameter.
        if "apiclick.aspx" in link:
            real = parse_qs(urlparse(link).query).get("url", [""])[0]
            if real:
                link = real
        title = _rss_child_text(item, "title")
        if not link.startswith("http") or not title:
            continue
        hits.append(SearchHit(
            url=link,
            title=title,
            snippet=_strip_html(_rss_child_text(item, "description")),
            source=_rss_child_text(item, "source"),
            date=_format_rss_date(_rss_child_text(item, "pubdate")),
        ))
    return hits


def _news_google_rss(query: str, region: SearchRegion, max_results: int) -> list[SearchHit]:
    lang, country = region.language, region.country
    url = (
        f"https://news.google.com/rss/search?q={quote_plus(query)}"
        f"&hl={lang}&gl={country}&ceid={country}:{lang}"
    )
    result = http_get(url, provider_key="google_news")
    if not result.ok:
        raise RuntimeError(f"google_news: {result.error or result.status}")

    hits: list[SearchHit] = []
    for item in _rss_items(result.text):
        title = _rss_child_text(item, "title")
        if not title:
            continue
        source_el = None
        for child in item:
            if isinstance(child.tag, str) and etree.QName(child).localname == "source":
                source_el = child
                break
        source_name = (source_el.text or "").strip() if source_el is not None else ""
        source_url = source_el.get("url", "") if source_el is not None else ""
        # Google News links are opaque redirects that cannot be scraped;
        # keep the item for its headline/source/date and mark it so the
        # pipeline uses the snippet instead of downloading the page.
        link = _rss_child_text(item, "link") or source_url
        if source_name and title.endswith(f" - {source_name}"):
            title = title[: -len(f" - {source_name}")]
        hits.append(SearchHit(
            url=link or source_url,
            title=title,
            snippet="",
            source=source_name,
            date=_format_rss_date(_rss_child_text(item, "pubdate")),
            no_scrape=True,
        ))
        if len(hits) >= max_results * 2:
            break
    return hits


# --------------------------------------------------------------------------
# Ranking and cascading
# --------------------------------------------------------------------------

def rank_hits(hits: list[SearchHit], max_results: int, per_domain_cap: int = 2) -> list[SearchHit]:
    """Filter junk, de-duplicate, and order hits by position + authority."""
    best: dict[str, SearchHit] = {}
    for position, hit in enumerate(hits):
        if not hit.url.startswith("http") or is_blocked(hit.url):
            continue
        key = normalize_url(hit.url)
        position_score = 1.0 / (position + 1)
        score = position_score + trust_boost(hit.url)
        existing = best.get(key)
        if existing is None:
            hit.score = score
            hit.no_scrape = hit.no_scrape or is_noscrape(hit.url)
            best[key] = hit
        else:
            # Seen from multiple providers/positions: corroboration bonus.
            existing.score += 0.3 + position_score * 0.5
            if not existing.snippet and hit.snippet:
                existing.snippet = hit.snippet

    ranked = sorted(best.values(), key=lambda h: h.score, reverse=True)
    per_domain: dict[str, int] = {}
    final: list[SearchHit] = []
    for hit in ranked:
        domain = domain_of(hit.url)
        if per_domain.get(domain, 0) >= per_domain_cap:
            continue
        per_domain[domain] = per_domain.get(domain, 0) + 1
        final.append(hit)
        if len(final) >= max_results:
            break
    return final


Provider = Callable[[str, SearchRegion, int], list[SearchHit]]

WEB_PROVIDERS: tuple[tuple[str, Provider], ...] = (
    ("ddg_html", _search_ddg_html),
    ("ddg_lite", _search_ddg_lite),
    ("bing_rss", _search_bing_rss),
)

NEWS_PROVIDERS: tuple[tuple[str, Provider], ...] = (
    ("bing_news", _news_bing_rss),
    ("google_news", _news_google_rss),
)


def _run_cascade(
    providers: tuple[tuple[str, Provider], ...],
    query: str,
    region: SearchRegion,
    max_results: int,
) -> list[SearchHit]:
    """Try providers in order; retry the whole cascade with backoff.

    A provider is skipped while its circuit breaker is open. Results from
    the first provider that returns enough hits are used directly; if a
    provider returns only a few, the next one's results are merged in so
    partial availability still produces a full answer.
    """
    for attempt in range(1, SEARCH_RETRY_ATTEMPTS + 1):
        collected: list[SearchHit] = []
        for name, provider in providers:
            if breaker.is_open(name):
                logger.info("Provider '%s' in cooldown, skipping", name)
                continue
            try:
                hits = provider(query, region, max_results)
            except Exception as exc:  # noqa: BLE001 - cascade must continue
                logger.warning("Provider '%s' failed: %s", name, exc)
                continue
            if hits:
                logger.info("Provider '%s' returned %d hits", name, len(hits))
                collected.extend(hits)
            if len(collected) >= max_results * 2:
                break
        if collected:
            return rank_hits(collected, max_results)
        if attempt < SEARCH_RETRY_ATTEMPTS:
            delay = 1.5 * (2 ** (attempt - 1))
            logger.info("All providers empty (attempt %d), retrying in %.1fs", attempt, delay)
            time.sleep(delay)
    return []


def web_search(query: str, region: SearchRegion, max_results: int) -> list[SearchHit]:
    """Cascading web search across DDG HTML, DDG Lite, and Bing RSS."""
    return _run_cascade(WEB_PROVIDERS, query, region, max_results)


def news_search(query: str, region: SearchRegion, max_results: int) -> list[SearchHit]:
    """Cascading news search; falls back to web search as a last resort."""
    hits = _run_cascade(NEWS_PROVIDERS, query, region, max_results)
    if hits:
        return hits
    logger.info("News providers exhausted, falling back to web search")
    return _run_cascade(WEB_PROVIDERS, f"{query} news", region, max_results)
