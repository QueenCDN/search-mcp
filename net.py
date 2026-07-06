"""HTTP layer: pooled session, bounded downloads, rate limiting, breakers.

All outbound traffic goes through :func:`http_get` / :func:`http_post`:

* one shared :class:`requests.Session` reuses TCP/TLS connections,
* responses are streamed and cut off at ``MAX_HTML_BYTES`` so a huge page
  can never balloon memory,
* per-provider :class:`RateLimiter` enforces a polite minimum interval
  between requests to the same backend,
* per-provider :class:`CircuitBreaker` takes a backend out of rotation
  with exponential cooldown after failures or explicit 403/429 blocks,
  which is what actually stops rate-limit storms.
"""

from __future__ import annotations

import random
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

from utils import (
    MAX_HTML_BYTES,
    PAGE_TIMEOUT,
    PROVIDER_COOLDOWN,
    PROVIDER_MIN_INTERVAL,
    USER_AGENT,
    get_logger,
)

logger = get_logger("net")

# A small pool of realistic desktop browser identities. Rotating between a
# few plausible UAs makes traffic look less like a single scripted client
# without pretending to be an exotic device.
_USER_AGENTS: tuple[str, ...] = (
    USER_AGENT,
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 12.7; rv:126.0) "
    "Gecko/20100101 Firefox/126.0",
)

_BASE_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def _build_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=8,
        pool_maxsize=8,
        max_retries=0,  # retries are handled explicitly by callers
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(_BASE_HEADERS)
    return session


# requests.Session is safe for concurrent .get() calls in practice (urllib3
# pools are thread-safe); one shared session gives connection reuse across
# the scraper worker threads.
_SESSION = _build_session()


def pick_user_agent() -> str:
    return random.choice(_USER_AGENTS)


# --------------------------------------------------------------------------
# Rate limiting and circuit breaking (per provider key)
# --------------------------------------------------------------------------

class RateLimiter:
    """Enforces a minimum interval between requests per provider key."""

    def __init__(self, min_interval: float = PROVIDER_MIN_INTERVAL) -> None:
        self._min_interval = min_interval
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str) -> None:
        """Block until it is polite to hit ``key`` again."""
        while True:
            with self._lock:
                now = time.monotonic()
                last = self._last_request.get(key, 0.0)
                wait_for = self._min_interval - (now - last)
                if wait_for <= 0:
                    self._last_request[key] = now
                    return
            # Sleep outside the lock; add jitter so callers don't thunder.
            time.sleep(min(wait_for, self._min_interval) + random.uniform(0.05, 0.25))


@dataclass
class _BreakerState:
    consecutive_failures: int = 0
    open_until: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


class CircuitBreaker:
    """Takes failing providers out of rotation with exponential cooldown.

    A provider that returned 403/429 (or kept erroring) is skipped for a
    cooldown that doubles with each consecutive failure, capped at 15
    minutes. One success resets it. This converts "hammer a blocked
    endpoint and stay blocked forever" into "back off and recover".
    """

    _MAX_COOLDOWN = 900.0

    def __init__(self, base_cooldown: float = float(PROVIDER_COOLDOWN)) -> None:
        self._base = base_cooldown
        self._states: dict[str, _BreakerState] = {}
        self._registry_lock = threading.Lock()

    def _state(self, key: str) -> _BreakerState:
        with self._registry_lock:
            state = self._states.get(key)
            if state is None:
                state = _BreakerState()
                self._states[key] = state
            return state

    def is_open(self, key: str) -> bool:
        state = self._state(key)
        with state.lock:
            return time.monotonic() < state.open_until

    def record_success(self, key: str) -> None:
        state = self._state(key)
        with state.lock:
            state.consecutive_failures = 0
            state.open_until = 0.0

    def record_failure(self, key: str, blocked: bool = False) -> None:
        """Register a failure; ``blocked=True`` means an explicit 403/429."""
        state = self._state(key)
        with state.lock:
            state.consecutive_failures += 1
            exponent = state.consecutive_failures - 1
            cooldown = min(self._base * (2 ** exponent), self._MAX_COOLDOWN)
            if not blocked:
                # Generic errors get a shorter pause than explicit blocks.
                cooldown = min(cooldown, 60.0 * state.consecutive_failures)
            state.open_until = time.monotonic() + cooldown
            logger.warning(
                "Provider '%s' cooling down for %.0fs (failures=%d, blocked=%s)",
                key,
                cooldown,
                state.consecutive_failures,
                blocked,
            )

    def snapshot(self) -> dict[str, float]:
        """Remaining cooldown per provider, for logging/diagnostics."""
        now = time.monotonic()
        with self._registry_lock:
            return {
                key: max(0.0, state.open_until - now)
                for key, state in self._states.items()
                if state.open_until > now
            }


rate_limiter = RateLimiter()
breaker = CircuitBreaker()


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

@dataclass
class FetchResult:
    """Outcome of an HTTP fetch with the failure reason preserved."""

    ok: bool
    status: int = 0
    text: str = ""
    content_type: str = ""
    error: str = ""

    @property
    def blocked(self) -> bool:
        return self.status in (403, 429, 503)


_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""", re.IGNORECASE
)


def _decode_body(raw: bytes, content_type: str) -> str:
    """Decode HTML bytes using header charset, then meta charset, then UTF-8."""
    charset_match = re.search(r"charset=([a-zA-Z0-9_\-]+)", content_type)
    candidates = []
    if charset_match:
        candidates.append(charset_match.group(1))
    meta = _META_CHARSET_RE.search(raw[:4096])
    if meta:
        candidates.append(meta.group(1).decode("ascii", "ignore"))
    candidates.append("utf-8")
    for encoding in candidates:
        try:
            return raw.decode(encoding, errors="replace")
        except (LookupError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def _request(
    method: str,
    url: str,
    *,
    provider_key: Optional[str] = None,
    timeout: float = PAGE_TIMEOUT,
    max_bytes: int = MAX_HTML_BYTES,
    params: Optional[dict] = None,
    data: Optional[dict] = None,
    headers: Optional[dict[str, str]] = None,
) -> FetchResult:
    if provider_key:
        rate_limiter.wait(provider_key)

    request_headers = {"User-Agent": pick_user_agent()}
    if headers:
        request_headers.update(headers)

    try:
        with _SESSION.request(
            method,
            url,
            params=params,
            data=data,
            headers=request_headers,
            timeout=(5, timeout),
            stream=True,
            allow_redirects=True,
        ) as response:
            status = response.status_code
            content_type = response.headers.get("Content-Type", "")
            if status >= 400:
                if provider_key:
                    breaker.record_failure(provider_key, blocked=status in (403, 429, 503))
                return FetchResult(ok=False, status=status, content_type=content_type,
                                   error=f"HTTP {status}")

            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=65536):
                chunks.append(chunk)
                size += len(chunk)
                if size >= max_bytes:
                    logger.debug("Truncated download at %d bytes: %s", size, url)
                    break
            raw = b"".join(chunks)
    except requests.exceptions.Timeout:
        if provider_key:
            breaker.record_failure(provider_key)
        return FetchResult(ok=False, error=f"timeout after {timeout}s")
    except requests.exceptions.SSLError as exc:
        return FetchResult(ok=False, error=f"SSL error: {exc}")
    except requests.exceptions.ConnectionError as exc:
        if provider_key:
            breaker.record_failure(provider_key)
        return FetchResult(ok=False, error=f"connection error: {exc}")
    except requests.exceptions.RequestException as exc:
        return FetchResult(ok=False, error=f"request failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - network boundary safety net
        logger.exception("Unexpected error fetching %s", url)
        return FetchResult(ok=False, error=f"unexpected error: {exc}")

    if provider_key:
        breaker.record_success(provider_key)
    return FetchResult(
        ok=True,
        status=status,
        text=_decode_body(raw, content_type),
        content_type=content_type,
    )


def http_get(url: str, **kwargs) -> FetchResult:
    """GET ``url`` with pooling, size caps, and optional provider limits."""
    return _request("GET", url, **kwargs)


def http_post(url: str, **kwargs) -> FetchResult:
    """POST to ``url`` with pooling, size caps, and optional provider limits."""
    return _request("POST", url, **kwargs)


def get_json(
    url: str,
    *,
    timeout: float = PAGE_TIMEOUT,
    params: Optional[dict] = None,
    retries: int = 2,
) -> Optional[dict]:
    """Fetch and parse a JSON endpoint; returns None on any failure.

    Free public APIs (Open-Meteo, exchange-rate mirrors) throw occasional
    transient 5xx errors; a short retry recovers those without bothering
    the caller.
    """
    import json

    for attempt in range(retries + 1):
        result = http_get(url, timeout=timeout, params=params,
                          headers={"Accept": "application/json"})
        if result.ok:
            try:
                return json.loads(result.text)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("Invalid JSON from %s: %s", url, exc)
                return None
        transient = result.status >= 500 or not result.status
        if not transient or attempt == retries:
            logger.warning("JSON fetch failed for %s: %s", url, result.error or result.status)
            return None
        time.sleep(0.5 * (attempt + 1))
    return None
