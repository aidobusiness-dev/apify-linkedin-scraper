"""LinkedIn post & comment scraper via public JSON-LD.

LinkedIn embeds a <script type="application/ld+json"> block on every public
post page containing a SocialMediaPosting object with full post data,
including up to ~10 comments with author names.

No cookie, no browser, just HTTP GET + JSON parse.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from .config import (
    HTTP_TIMEOUT,
    MAX_RETRIES,
    MIN_TEXT_LENGTH,
    POST_DELAY_MIN,
    POST_DELAY_MAX,
    RETRY_BASE_DELAY,
    random_ua,
)
from .util import sleep_random

log = logging.getLogger("li_scraper.scraper")

# Rate-limit tracking: consecutive 429s across requests
_rate_limit_hits = 0
_RATE_LIMIT_THRESHOLD = 5  # abort after this many consecutive 429s

# Shared session for TCP connection reuse (keep-alive)
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """Get or create a shared requests.Session for connection pooling."""
    global _session
    if _session is None:
        _session = requests.Session()
        ua = random_ua()
        _session.headers.update({
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        })
        log.info("HTTP session created (UA: %s)", ua[:50])
    return _session


def validate_linkedin_url(url: str) -> bool:
    """Check that a URL points to LinkedIn (SSRF prevention)."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or ""
        return (
            parsed.scheme in ("http", "https")
            and (host == "linkedin.com" or host.endswith(".linkedin.com"))
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# HTTP fetch with retry + backoff
# ---------------------------------------------------------------------------
def _fetch_html(
    url: str,
    proxies: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """GET a URL with retry logic and exponential backoff.

    Returns HTML string on success, None on failure.
    Tracks consecutive 429s — aborts the run if LinkedIn is clearly blocking.
    """
    global _rate_limit_hits

    if _rate_limit_hits >= _RATE_LIMIT_THRESHOLD:
        log.warning("Rate-limit threshold reached (%d consecutive 429s) — skipping %s",
                     _rate_limit_hits, url[:60])
        return None

    session = _get_session()

    last_status = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(
                url,
                timeout=HTTP_TIMEOUT,
                proxies=proxies,
                allow_redirects=True,
            )
            last_status = resp.status_code

            if resp.status_code == 429 or resp.status_code >= 500:
                if resp.status_code == 429:
                    _rate_limit_hits += 1
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("HTTP %d for %s — retrying in %.1fs (attempt %d/%d)",
                                resp.status_code, url[:60], delay, attempt + 1, MAX_RETRIES)
                    time.sleep(delay)
                    continue
                # Last attempt — fall through to return None
                break

            if resp.status_code != 200:
                log.warning("HTTP %d for %s", resp.status_code, url[:60])
                return None

            # Success — reset rate limit counter
            _rate_limit_hits = 0

            # Redirect target validation + auth wall detection
            final_url = resp.url
            if any(kw in final_url for kw in ("/login", "/authwall", "/checkpoint", "/signin")):
                log.debug("Auth wall for %s (private or restricted post)", url[:60])
                return None

            if not validate_linkedin_url(final_url):
                log.warning("Redirected to non-LinkedIn URL: %s", final_url[:60])
                return None

            return resp.text

        except requests.Timeout:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                log.warning("Timeout for %s — retrying in %.1fs", url[:60], delay)
                time.sleep(delay)
                continue
            break

        except requests.RequestException as exc:
            log.warning("Request error for %s: %s", url[:60], exc)
            return None

    log.warning("All %d retries exhausted for %s (last status: %s)", MAX_RETRIES, url[:60], last_status)
    return None


# ---------------------------------------------------------------------------
# JSON-LD extraction
# ---------------------------------------------------------------------------
_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']?application/ld\+json["\']?[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _extract_jsonld(html: str) -> Optional[dict[str, Any]]:
    """Find the SocialMediaPosting JSON-LD block in HTML."""
    for block_str in _JSONLD_RE.findall(html):
        try:
            data = json.loads(block_str.strip())
        except json.JSONDecodeError:
            continue

        # Direct match
        if isinstance(data, dict) and data.get("@type") == "SocialMediaPosting":
            return data

        # @graph wrapper
        if isinstance(data, dict) and "@graph" in data and isinstance(data["@graph"], list):
            for item in data["@graph"]:
                if isinstance(item, dict) and item.get("@type") == "SocialMediaPosting":
                    return item

        # Array
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") == "SocialMediaPosting":
                    return item

    return None


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------
def _get_interaction_count(data: dict[str, Any], action_type: str) -> int:
    """Extract a specific interaction count from interactionStatistic."""
    stats = data.get("interactionStatistic", [])
    if not isinstance(stats, list):
        stats = [stats]
    for stat in stats:
        if not isinstance(stat, dict):
            continue
        itype = stat.get("interactionType", "")
        if isinstance(itype, dict):
            itype = itype.get("@type", "")
        if action_type.lower() in str(itype).lower():
            try:
                return int(float(stat.get("userInteractionCount", 0)))
            except (TypeError, ValueError):
                pass
    return 0


# Fallback: extract engagement from HTML when JSON-LD has no stats
_OG_LIKES_RE = re.compile(r'(\d[\d,]*)\s*(?:likes?|reactions?)', re.IGNORECASE)
_OG_COMMENTS_RE = re.compile(r'(\d[\d,]*)\s*comments?', re.IGNORECASE)
_OG_SHARES_RE = re.compile(r'(\d[\d,]*)\s*(?:shares?|reposts?)', re.IGNORECASE)
_OG_DESC_RE = re.compile(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']*)["\']', re.IGNORECASE)


def _extract_og_engagement(html: str) -> dict[str, int]:
    """Fallback: try to extract engagement counts from OG description or page text.

    LinkedIn sometimes puts "142 likes, 23 comments" in the og:description meta tag
    even when JSON-LD interactionStatistic is empty.
    """
    # Try og:description first (most reliable)
    og_match = _OG_DESC_RE.search(html)
    og_text = og_match.group(1) if og_match else ""

    # Also check a small window around social-counts in page HTML
    counts_text = og_text

    result: dict[str, int] = {}

    likes_m = _OG_LIKES_RE.search(counts_text)
    if likes_m:
        result["likes"] = int(likes_m.group(1).replace(",", ""))

    comments_m = _OG_COMMENTS_RE.search(counts_text)
    if comments_m:
        result["comments"] = int(comments_m.group(1).replace(",", ""))

    shares_m = _OG_SHARES_RE.search(counts_text)
    if shares_m:
        result["shares"] = int(shares_m.group(1).replace(",", ""))

    return result


_ACTIVITY_RE = re.compile(r"activity-(\d+)")
_PROFILE_RE = re.compile(r"linkedin\.com/in/([^/?#]+)")


def _parse_activity_urn(url: str) -> str:
    m = _ACTIVITY_RE.search(url)
    return f"urn:li:activity:{m.group(1)}" if m else ""


def _parse_profile_id(url: str) -> str:
    m = _PROFILE_RE.search(url)
    return m.group(1) if m else ""


def _extract_comments(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract comments from the JSON-LD comment array.

    LinkedIn includes up to ~10 comments in the JSON-LD block.
    Each comment has author name, text, and date.
    """
    raw = data.get("comment", [])
    if not isinstance(raw, list):
        raw = [raw]

    comments: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        author = c.get("author", {})
        if not isinstance(author, dict):
            continue

        name = author.get("name", "")
        if not name:
            continue

        comments.append({
            "authorName": name,
            "authorProfileUrl": author.get("url", ""),
            "text": c.get("text", ""),
            "postedAt": c.get("datePublished", ""),
        })

    return comments


# ---------------------------------------------------------------------------
# Post normalisation — clean output schema
# ---------------------------------------------------------------------------
def _normalise_post(
    data: dict[str, Any],
    source_url: str,
    keyword: str = "",
    include_comments: bool = True,
) -> dict[str, Any]:
    """Convert a JSON-LD SocialMediaPosting to a clean output dict.

    Single field per concept — no duplicate aliases.
    """
    now = datetime.now(timezone.utc)

    # Text
    text = data.get("articleBody", "") or data.get("description", "") or ""

    # Author
    author = data.get("author", {}) or {}
    if isinstance(author, list):
        author = author[0] if author else {}

    author_name = author.get("name", "") or ""
    author_url = author.get("url", "") or ""
    author_headline = author.get("jobTitle", "") or ""

    # Image
    img = author.get("image", {})
    author_image = ""
    if isinstance(img, dict):
        author_image = img.get("url", "") or img.get("contentUrl", "") or ""
    elif isinstance(img, str):
        author_image = img

    # Date
    date_published = data.get("datePublished", "") or ""

    # Interactions
    likes = _get_interaction_count(data, "LikeAction")
    num_comments = _get_interaction_count(data, "CommentAction")
    shares = _get_interaction_count(data, "ShareAction")

    # Comments
    comments = _extract_comments(data) if include_comments else []

    # Clean URL (strip query params) and ensure https
    post_url = source_url.split("?")[0] if source_url else ""
    if post_url.startswith("http://"):
        post_url = "https://" + post_url[7:]
    if author_url.startswith("http://"):
        author_url = "https://" + author_url[7:]
    if author_image.startswith("http://"):
        author_image = "https://" + author_image[7:]

    result: dict[str, Any] = {
        "urn": _parse_activity_urn(source_url),
        "postUrl": post_url,
        "text": text,
        "authorName": author_name,
        "authorHeadline": author_headline,
        "authorProfileUrl": author_url,
        "authorProfileId": _parse_profile_id(author_url),
        "authorImageUrl": author_image,
        "numLikes": likes,
        "numComments": num_comments,
        "numShares": shares,
        "postedAt": date_published or now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "keyword": keyword,
        "scrapedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if include_comments:
        result["comments"] = comments

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def scrape_post(
    url: str,
    keyword: str = "",
    include_comments: bool = True,
    proxies: Optional[dict[str, str]] = None,
) -> Optional[dict[str, Any]]:
    """Scrape a single LinkedIn post URL.

    Returns a normalised post dict, or None if the post couldn't be scraped.
    Uses OG meta tag fallback when JSON-LD has no engagement stats.
    """
    html = _fetch_html(url, proxies=proxies)
    if not html:
        return None

    jsonld = _extract_jsonld(html)
    if not jsonld:
        log.debug("No JSON-LD in %s", url[:60])
        return None

    text = jsonld.get("articleBody", "") or jsonld.get("description", "") or ""
    if len(text) < MIN_TEXT_LENGTH:
        log.debug("Post too short (%d chars): %s", len(text), url[:60])
        return None

    post = _normalise_post(jsonld, source_url=url, keyword=keyword, include_comments=include_comments)

    # Fallback: if JSON-LD had no engagement stats, try OG/HTML extraction
    if post["numLikes"] == 0 and post["numComments"] == 0:
        og = _extract_og_engagement(html)
        if og:
            post["numLikes"] = og.get("likes", 0)
            post["numComments"] = og.get("comments", 0)
            post["numShares"] = og.get("shares", post["numShares"])
            log.debug("OG fallback: %d likes, %d comments for %s",
                      post["numLikes"], post["numComments"], url[:60])

    return post


def scrape_urls(
    urls: list[str],
    include_comments: bool = True,
    proxies: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    """Scrape a batch of known LinkedIn post URLs.

    Args:
        urls: List of LinkedIn post URLs.
        include_comments: Whether to extract comments.
        proxies: Optional HTTP proxy dict for requests.

    Returns:
        List of scraped post dicts.
    """
    results: list[dict[str, Any]] = []

    for idx, url in enumerate(urls):
        if idx > 0:
            sleep_random(POST_DELAY_MIN, POST_DELAY_MAX)

        log.info("[%d/%d] %s", idx + 1, len(urls), url[:80])
        post = scrape_post(url, include_comments=include_comments, proxies=proxies)

        if post:
            results.append(post)
            log.info("  OK: %s (%d likes, %d comments)",
                     post["authorName"][:30], post["numLikes"], len(post.get("comments", [])))

    return results
