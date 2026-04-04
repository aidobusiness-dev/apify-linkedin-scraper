"""LinkedIn Post & Comment Scraper — Apify Actor.

Scrapes LinkedIn posts and comments by keyword or direct URL.
No browser, no cookie required — uses public JSON-LD data.

Unique feature: keyword-based post discovery via DuckDuckGo/Google CSE.

Modes:
  1. Apify platform — reads input via Actor.get_input(), pushes via Actor.push_data()
  2. Local CLI — reads from args, writes to output/posts.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
from pathlib import Path
from typing import Any

from .config import (
    KW_DELAY_MIN,
    KW_DELAY_MAX,
    LOG_FORMAT,
    LOG_DATE_FMT,
    MAX_KEYWORDS,
    MAX_POSTS_PER_KEYWORD,
    MAX_URLS,
    POST_DELAY_MIN,
    POST_DELAY_MAX,
    PROXY_KW_DELAY_MIN,
    PROXY_KW_DELAY_MAX,
    PROXY_POST_DELAY_MIN,
    PROXY_POST_DELAY_MAX,
)
from .discovery import discover_urls
from .scraper import scrape_post, scrape_urls, validate_linkedin_url
from .util import sleep_random

log = logging.getLogger("li_scraper.main")


# ---------------------------------------------------------------------------
# Date filter mapping (user input -> DDG/Brave format)
# ---------------------------------------------------------------------------
_DATE_FILTER_MAP = {
    "past-day": "d",
    "past-24h": "d",
    "past-week": "w",
    "past-month": "m",
}


# ---------------------------------------------------------------------------
# Core: scrape by keywords (post discovery + extraction)
# ---------------------------------------------------------------------------
def scrape_by_keywords(
    keywords: list[str],
    max_per_kw: int = 15,
    date_filter: str = "past-week",
    include_comments: bool = True,
    proxies: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Discover and scrape LinkedIn posts for each keyword.

    Returns a list of batches (one per keyword) for incremental pushing.
    """
    if len(keywords) > MAX_KEYWORDS:
        log.warning("Capping keywords from %d to %d", len(keywords), MAX_KEYWORDS)
        keywords = keywords[:MAX_KEYWORDS]

    df = _DATE_FILTER_MAP.get(date_filter, "w")
    all_posts: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for kw_idx, keyword in enumerate(keywords):
        if kw_idx > 0:
            sleep_random(KW_DELAY_MIN, KW_DELAY_MAX)

        log.info("[kw %d/%d] %r", kw_idx + 1, len(keywords), keyword[:80])

        urls = discover_urls(keyword[:200], max_results=max_per_kw, date_filter=df)
        if not urls:
            log.warning("  No URLs found for %r", keyword[:80])
            continue

        new_urls = [u for u in urls if u not in seen_urls]
        seen_urls.update(new_urls)

        log.info("  %d URLs (%d new), scraping...", len(urls), len(new_urls))
        kw_posts: list[dict[str, Any]] = []

        for idx, url in enumerate(new_urls):
            if idx > 0:
                sleep_random(POST_DELAY_MIN, POST_DELAY_MAX)

            post = scrape_post(url, keyword=keyword, include_comments=include_comments, proxies=proxies)
            if post:
                kw_posts.append(post)
                if len(kw_posts) >= max_per_kw:
                    break

        log.info("  %r: %d posts", keyword[:80], len(kw_posts))
        all_posts.extend(kw_posts)

    return all_posts


# ---------------------------------------------------------------------------
# Core: scrape direct URLs
# ---------------------------------------------------------------------------
def scrape_by_urls(
    urls: list[str],
    include_comments: bool = True,
    proxies: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Scrape a list of direct LinkedIn post URLs."""
    if len(urls) > MAX_URLS:
        log.warning("Capping URLs from %d to %d", len(urls), MAX_URLS)
        urls = urls[:MAX_URLS]

    # Validate all URLs are LinkedIn
    valid_urls = []
    for url in urls:
        if validate_linkedin_url(url):
            valid_urls.append(url)
        else:
            log.warning("Skipping non-LinkedIn URL: %s", url[:80])

    return scrape_urls(valid_urls, include_comments=include_comments, proxies=proxies)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
def _dedup(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for p in posts:
        key = p.get("urn") or p.get("postUrl") or ""
        if not key:
            unique.append(p)  # can't dedup without identifier
            continue
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


# ---------------------------------------------------------------------------
# Apify entry point
# ---------------------------------------------------------------------------
async def apify_main() -> None:
    """Async entry point for Apify platform execution."""
    from apify import Actor  # type: ignore[import-untyped]

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATE_FMT)

    async with Actor:
        actor_input = await Actor.get_input() or {}
        log.info("Apify input: %s", list(actor_input.keys()))

        # Validate input
        raw_keywords = actor_input.get("keywords")
        has_urls = bool(actor_input.get("startUrls") or actor_input.get("urls"))

        # Normalise keywords: accept string or list
        keywords: list[str] = []
        if raw_keywords:
            if isinstance(raw_keywords, str):
                keywords = [raw_keywords]
            elif isinstance(raw_keywords, list):
                keywords = [str(k) for k in raw_keywords]

        if not keywords and not has_urls:
            await Actor.fail(status_message="Input must contain 'keywords' or 'startUrls'.")
            return

        # Proxy setup — store config for per-request rotation
        proxy_config = None
        proxy_config_input = actor_input.get("proxyConfiguration")
        if proxy_config_input:
            try:
                proxy_config = await Actor.create_proxy_configuration(
                    actor_proxy_input=proxy_config_input,
                )
                if proxy_config:
                    # Verify the config works
                    test_url = await proxy_config.new_url()
                    if test_url and test_url.startswith(("http://", "https://")):
                        log.info("Proxy configured (rotating per request)")
                    else:
                        log.warning("Invalid proxy URL schema — continuing without proxy")
                        proxy_config = None
            except Exception as exc:
                log.warning("Proxy setup failed: %s — continuing without proxy", exc)
                proxy_config = None

        async def _get_proxies(session_id: str = "") -> dict[str, str] | None:
            """Get fresh proxy dict for each request (IP rotation)."""
            if not proxy_config:
                return None
            try:
                url = await proxy_config.new_url(session_id=session_id or None)
                return {"http": url, "https": url} if url else None
            except Exception:
                return None

        include_comments = actor_input.get("scrapeComments", True)
        total_pushed = 0

        # Adaptive delays: faster with proxy (IP rotation handles rate limiting)
        post_delay_min = PROXY_POST_DELAY_MIN if proxy_config else POST_DELAY_MIN
        post_delay_max = PROXY_POST_DELAY_MAX if proxy_config else POST_DELAY_MAX
        kw_delay_min = PROXY_KW_DELAY_MIN if proxy_config else KW_DELAY_MIN
        kw_delay_max = PROXY_KW_DELAY_MAX if proxy_config else KW_DELAY_MAX

        # Estimate total posts and warn if no proxy at scale
        est_posts = len(keywords) * int(actor_input.get("maxPostsPerKeyword", 15))
        if has_urls:
            raw_urls_est = actor_input.get("startUrls") or actor_input.get("urls") or []
            est_posts += len(raw_urls_est)
        if not proxy_config and est_posts > 10:
            log.warning(
                "No proxy configured for ~%d posts. LinkedIn may rate-limit. "
                "Recommended: enable proxy in actor settings for reliable results.",
                est_posts,
            )

        # Keyword mode — push incrementally per keyword
        if keywords:
            try:
                max_per_kw = min(int(actor_input.get("maxPostsPerKeyword", 15)), MAX_POSTS_PER_KEYWORD)
            except (TypeError, ValueError):
                max_per_kw = 15
            date_filter = actor_input.get("dateFilter", "past-week")

            if len(keywords) > MAX_KEYWORDS:
                log.warning("Capping keywords from %d to %d", len(keywords), MAX_KEYWORDS)
                keywords = keywords[:MAX_KEYWORDS]

            log.info("Keyword mode: %d keywords, max=%d, filter=%s",
                     len(keywords), max_per_kw, date_filter)

            df = _DATE_FILTER_MAP.get(date_filter, "w")
            seen_urls: set[str] = set()
            seen_urns: set[str] = set()

            for kw_idx, keyword in enumerate(keywords):
                if kw_idx > 0:
                    await asyncio.sleep(random.uniform(kw_delay_min, kw_delay_max))

                log.info("[kw %d/%d] %r", kw_idx + 1, len(keywords), keyword[:80])
                urls = discover_urls(keyword[:200], max_results=max_per_kw, date_filter=df)
                if not urls:
                    continue

                # Filter: only new LinkedIn URLs
                new_urls = [
                    u for u in urls
                    if u not in seen_urls and validate_linkedin_url(u)
                ]
                seen_urls.update(new_urls)

                kw_posts: list[dict[str, Any]] = []
                for idx, url in enumerate(new_urls):
                    if idx > 0:
                        await asyncio.sleep(random.uniform(post_delay_min, post_delay_max))
                    proxies = await _get_proxies()
                    post = scrape_post(url, keyword=keyword, include_comments=include_comments, proxies=proxies)
                    if post:
                        # Cross-keyword dedup by URN
                        urn = post.get("urn") or post.get("postUrl") or ""
                        if urn and urn in seen_urns:
                            continue
                        if urn:
                            seen_urns.add(urn)
                        kw_posts.append(post)
                        if len(kw_posts) >= max_per_kw:
                            break

                if kw_posts:
                    await Actor.push_data(kw_posts)
                    total_pushed += len(kw_posts)
                    log.info("  Pushed %d posts for %r (total: %d)", len(kw_posts), keyword[:80], total_pushed)

        # URL mode — with per-request proxy rotation
        if has_urls:
            raw_urls = actor_input.get("startUrls") or actor_input.get("urls") or []
            urls = [
                u["url"] if isinstance(u, dict) else str(u)
                for u in raw_urls
            ]
            if len(urls) > MAX_URLS:
                log.warning("Capping URLs from %d to %d", len(urls), MAX_URLS)
                urls = urls[:MAX_URLS]

            # Validate URLs
            valid_urls = [u for u in urls if validate_linkedin_url(u)]
            if len(valid_urls) < len(urls):
                log.warning("Skipped %d non-LinkedIn URLs", len(urls) - len(valid_urls))

            log.info("URL mode: %d URLs", len(valid_urls))

            url_posts: list[dict[str, Any]] = []
            for idx, url in enumerate(valid_urls):
                if idx > 0:
                    await asyncio.sleep(random.uniform(post_delay_min, post_delay_max))
                proxies = await _get_proxies()
                post = scrape_post(url, include_comments=include_comments, proxies=proxies)
                if post:
                    url_posts.append(post)

            if url_posts:
                await Actor.push_data(url_posts)
                total_pushed += len(url_posts)

        if total_pushed == 0:
            log.warning("No posts found")

        log.info("Run complete: %d posts pushed", total_pushed)


# ---------------------------------------------------------------------------
# Local CLI
# ---------------------------------------------------------------------------
def local_main() -> None:
    """CLI entry point for local execution."""
    # Configure logging here (not at module level)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATE_FMT)

    parser = argparse.ArgumentParser(
        description="LinkedIn Post & Comment Scraper — local runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 -m src.main --keywords 'AI automation' 'digital transformation'\n"
            "  python3 -m src.main --urls 'https://linkedin.com/posts/...'\n"
            "  python3 -m src.main --keywords 'AI MKB' --no-comments --limit 5\n"
        ),
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--keywords", nargs="+", metavar="KW", help="Search keywords")
    input_group.add_argument("--urls", nargs="+", metavar="URL", help="Direct LinkedIn post URLs")

    parser.add_argument("--limit", type=int, default=15, help="Max posts per keyword (default: 15)")
    parser.add_argument(
        "--date-filter", default="past-week",
        choices=["past-day", "past-week", "past-month"],
        help="Recency filter (default: past-week)",
    )
    parser.add_argument("--no-comments", action="store_true", help="Skip comment extraction")
    parser.add_argument("--output", default="output/posts.json", help="Output JSON path")

    args = parser.parse_args()

    posts: list[dict[str, Any]] = []

    if args.keywords:
        posts = scrape_by_keywords(
            keywords=args.keywords,
            max_per_kw=args.limit,
            date_filter=args.date_filter,
            include_comments=not args.no_comments,
        )
    elif args.urls:
        posts = scrape_by_urls(
            urls=args.urls,
            include_comments=not args.no_comments,
        )

    posts = _dedup(posts)

    # Write output
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(posts, fh, ensure_ascii=False, indent=2)

    log.info("Wrote %d posts to %s", len(posts), out_path)

    # Summary
    print(f"\n{'='*60}")
    print(f"RESULTS: {len(posts)} posts")
    print(f"{'='*60}")
    for i, p in enumerate(posts[:10]):
        n_comments = len(p.get("comments", []))
        print(f"\n[{i+1}] {p.get('authorName', '?')[:40]}")
        print(f"    {p.get('text', '')[:120]}")
        print(f"    Likes: {p.get('numLikes', 0)}  Comments: {p.get('numComments', 0)} (scraped: {n_comments})")
        print(f"    URL: {p.get('postUrl', '')[:70]}")

    if len(posts) > 10:
        print(f"\n  ... and {len(posts) - 10} more. See {out_path}")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def _is_apify() -> bool:
    return bool(os.getenv("APIFY_IS_AT_HOME") or os.getenv("ACTOR_ID"))


if __name__ == "__main__":
    if _is_apify():
        asyncio.run(apify_main())
    else:
        local_main()
