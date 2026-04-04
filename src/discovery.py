"""URL discovery — find LinkedIn post URLs via DuckDuckGo and Google Custom Search.

This is the actor's unique differentiator: keyword-based post discovery
using free search engines, no LinkedIn cookie required.

Search engines:
  - DuckDuckGo (default) — free, no API key, good for small runs
  - Google Custom Search (optional) — user provides own free API key,
    better results, 100 free queries/day
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

import requests

from .config import HTTP_TIMEOUT
from .util import sleep_random

log = logging.getLogger("li_scraper.discovery")

try:
    from ddgs import DDGS as _DDGS  # type: ignore[import-untyped]

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clean_url(url: str) -> str:
    """Strip tracking params and ensure https."""
    parsed = urlparse(url)
    clean = parsed._replace(query="", fragment="").geturl()
    if clean.startswith("http://"):
        clean = "https://" + clean[7:]
    return clean


def _is_post_url(url: str) -> bool:
    return "linkedin.com/posts/" in url or "linkedin.com/feed/update/" in url


# ---------------------------------------------------------------------------
# DuckDuckGo (via ddgs library — handles bot challenges)
# ---------------------------------------------------------------------------
def find_urls_ddg(
    keyword: str,
    max_results: int = 15,
    date_filter: str = "w",
) -> list[str]:
    """Search DuckDuckGo for LinkedIn post URLs.

    Uses multiple query variants to maximise hit rate.
    Free, no API key required. Includes backoff on rate limiting.
    """
    if not _HAS_DDGS:
        log.warning("ddgs library not installed — pip install ddgs")
        return []

    query_variants = [
        f'"linkedin.com/posts" {keyword}',
        f"site:linkedin.com/posts/ {keyword}",
        f"{keyword} linkedin posts",
    ]

    urls: list[str] = []
    seen: set[str] = set()
    consecutive_failures = 0

    for qi, query in enumerate(query_variants):
        if len(urls) >= max_results:
            break

        # Backoff: increase delay after failures (DDG rate limiting)
        if qi > 0:
            base_delay = 1.0 + (consecutive_failures * 2.0)
            sleep_random(base_delay, base_delay + 1.0)

        # Bail out if DDG is clearly blocking us
        if consecutive_failures >= 2:
            log.warning("DDG rate-limited — stopping after %d failures", consecutive_failures)
            break

        log.info("DDG: %r (filter=%s)", query[:80], date_filter)
        try:
            with _DDGS() as ddg:
                raw = ddg.text(
                    query,
                    timelimit=date_filter,
                    max_results=max_results * 3,
                )
        except Exception as exc:
            consecutive_failures += 1
            log.warning("DDG query failed (%d/2): %s", consecutive_failures, exc)
            continue

        found = 0
        for item in raw or []:
            url = item.get("href") or item.get("url") or ""
            if _is_post_url(url):
                cleaned = _clean_url(url)
                if cleaned not in seen:
                    seen.add(cleaned)
                    urls.append(cleaned)
                    found += 1
            if len(urls) >= max_results:
                break

        if found > 0:
            consecutive_failures = 0  # reset on success
            log.info("  DDG variant found %d URLs", found)
        else:
            consecutive_failures += 1

    log.info("DDG total: %d URLs for %r", len(urls), keyword[:80])
    return urls[:max_results]


# ---------------------------------------------------------------------------
# Google Custom Search (optional — user provides own free API key)
# ---------------------------------------------------------------------------
_GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"

# Date restriction mapping: DDG filter -> Google dateRestrict
_GOOGLE_DATE_MAP = {"d": "d1", "w": "w1", "m": "m1"}


def find_urls_google_cse(
    keyword: str,
    max_results: int = 15,
    date_filter: str = "w",
    api_key: str = "",
    cse_id: str = "",
) -> list[str]:
    """Search Google Custom Search for LinkedIn post URLs.

    Requires user's own API key + Custom Search Engine ID.
    Free tier: 100 queries/day (enough for most actor runs).

    Get your free key:
      1. https://console.cloud.google.com/ -> Enable "Custom Search API"
      2. Create API key in Credentials
      3. https://programmablesearchengine.google.com/ -> Create search engine
         with "Search the entire web" enabled, note the cx ID

    Args:
        keyword: Search term.
        max_results: Cap on URLs (Google CSE returns max 10 per request).
        date_filter: 'd' day, 'w' week, 'm' month.
        api_key: Google API key.
        cse_id: Custom Search Engine ID (cx).

    Returns:
        List of LinkedIn post URLs.
    """
    api_key = api_key or os.getenv("GOOGLE_API_KEY", "")
    cse_id = cse_id or os.getenv("GOOGLE_CSE_ID", "")

    if not api_key or not cse_id:
        return []

    date_restrict = _GOOGLE_DATE_MAP.get(date_filter, "w1")

    # Google CSE: max 10 results per request, do up to 2 pages
    urls: list[str] = []
    seen: set[str] = set()

    for start_index in (1, 11):
        remaining = max_results - len(urls)
        if remaining <= 0:
            break

        params: dict[str, Any] = {
            "key": api_key,
            "cx": cse_id,
            "q": f"site:linkedin.com/posts/ {keyword}",
            "num": min(10, remaining),
            "start": start_index,
            "dateRestrict": date_restrict,
        }

        log.info("Google CSE: %r (page=%d)", keyword, (start_index - 1) // 10 + 1)
        try:
            resp = requests.get(_GOOGLE_CSE_URL, params=params, timeout=HTTP_TIMEOUT)

            if resp.status_code == 429:
                log.warning("Google CSE rate limit hit — falling back to DDG")
                break
            if resp.status_code == 403:
                log.warning("Google CSE quota exceeded or invalid key — falling back to DDG")
                break

            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, json.JSONDecodeError):
            log.warning("Google CSE request failed for %r", keyword)
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            url = item.get("link", "")
            if _is_post_url(url):
                cleaned = _clean_url(url)
                if cleaned not in seen:
                    seen.add(cleaned)
                    urls.append(cleaned)

    if urls:
        log.info("Google CSE: %d URLs for %r", len(urls), keyword)

    return urls[:max_results]


# ---------------------------------------------------------------------------
# Combined discovery — tries all available engines
# ---------------------------------------------------------------------------
def discover_urls(
    keyword: str,
    max_results: int = 15,
    date_filter: str = "w",
) -> list[str]:
    """Find LinkedIn post URLs using all available search engines.

    Priority:
      1. Google Custom Search (if GOOGLE_API_KEY + GOOGLE_CSE_ID set)
      2. DuckDuckGo (always available, free)

    Results from all engines are merged and deduplicated.
    """
    seen: set[str] = set()
    all_urls: list[str] = []

    # Try Google CSE first (better quality results)
    google_urls = find_urls_google_cse(keyword, max_results, date_filter)
    for url in google_urls:
        if url not in seen:
            seen.add(url)
            all_urls.append(url)

    # Always also try DDG for additional coverage
    remaining = max_results - len(all_urls)
    if remaining > 0:
        ddg_urls = find_urls_ddg(keyword, remaining, date_filter)
        for url in ddg_urls:
            if url not in seen:
                seen.add(url)
                all_urls.append(url)

    if google_urls and all_urls:
        log.info("Combined: %d URLs for %r (Google: %d, DDG: %d new)",
                 len(all_urls), keyword, len(google_urls),
                 len(all_urls) - len(google_urls))

    return all_urls[:max_results]
