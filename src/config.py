"""Configuration constants for the LinkedIn Post & Comment Scraper."""

from __future__ import annotations

import random

# ---------------------------------------------------------------------------
# Logging (config only — basicConfig is called in main.py entrypoints)
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s -- %(message)s"
LOG_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
MAX_KEYWORDS: int = 20
MAX_POSTS_PER_KEYWORD: int = 25
MAX_URLS: int = 50
MIN_TEXT_LENGTH: int = 30

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
# (connect_timeout, read_timeout) — fail fast on unreachable hosts, wait for slow responses
HTTP_TIMEOUT: tuple[float, float] = (5.0, 15.0)

# Retry
MAX_RETRIES: int = 3
RETRY_BASE_DELAY: float = 2.0  # seconds, doubles each retry

# Rate-limiting delays (seconds) — without proxy
POST_DELAY_MIN: float = 0.5
POST_DELAY_MAX: float = 1.5
KW_DELAY_MIN: float = 1.5
KW_DELAY_MAX: float = 3.0

# Faster delays when proxy rotates IPs per request
PROXY_POST_DELAY_MIN: float = 0.2
PROXY_POST_DELAY_MAX: float = 0.6
PROXY_KW_DELAY_MIN: float = 0.5
PROXY_KW_DELAY_MAX: float = 1.0

# ---------------------------------------------------------------------------
# User-Agent rotation
# ---------------------------------------------------------------------------
USER_AGENTS: list[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.4 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) "
        "Gecko/20100101 Firefox/137.0"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) "
        "Gecko/20100101 Firefox/136.0"
    ),
]


def random_ua() -> str:
    return random.choice(USER_AGENTS)
