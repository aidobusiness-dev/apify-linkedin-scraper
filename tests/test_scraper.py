"""Tests for the LinkedIn Post & Comment Scraper."""

import json
from unittest.mock import MagicMock, patch

import pytest

import src.scraper as _scraper_mod
from src.scraper import (
    _extract_comments,
    _extract_jsonld,
    _extract_og_engagement,
    _fetch_html,
    _get_interaction_count,
    _normalise_post,
    _parse_activity_urn,
    _parse_profile_id,
    scrape_post,
    validate_linkedin_url,
)
from src.discovery import _clean_url, _is_post_url, find_urls_ddg, find_urls_google_cse
from src.main import _dedup, _DATE_FILTER_MAP


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
SAMPLE_JSONLD = {
    "@type": "SocialMediaPosting",
    "articleBody": "AI is transforming how SMEs operate. Here are 5 ways to get started with automation in your business...",
    "datePublished": "2026-04-01T10:00:00Z",
    "author": {
        "name": "John Doe",
        "url": "https://www.linkedin.com/in/john-doe",
        "jobTitle": "CEO at TechCorp",
        "image": {"url": "https://media.licdn.com/john.jpg"},
    },
    "interactionStatistic": [
        {
            "interactionType": {"@type": "LikeAction"},
            "userInteractionCount": 142,
        },
        {
            "interactionType": {"@type": "CommentAction"},
            "userInteractionCount": 23,
        },
        {
            "interactionType": {"@type": "ShareAction"},
            "userInteractionCount": 8,
        },
    ],
    "comment": [
        {
            "author": {
                "name": "Jane Smith",
                "url": "https://www.linkedin.com/in/jane-smith",
            },
            "text": "Great insight! We did something similar.",
            "datePublished": "2026-04-02T08:15:00Z",
        },
        {
            "author": {
                "name": "Bob Wilson",
                "url": "",
            },
            "text": "Interesting perspective on automation.",
            "datePublished": "2026-04-02T09:00:00Z",
        },
    ],
}

SAMPLE_HTML = (
    '<html><head><script type="application/ld+json">'
    + json.dumps(SAMPLE_JSONLD)
    + "</script></head><body></body></html>"
)

SAMPLE_URL = "https://www.linkedin.com/posts/john-doe-ai-transforming-activity-7312345678901234567-AbCd"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
class TestUrlHelpers:
    def test_clean_url_strips_query(self):
        url = "https://linkedin.com/posts/test-123?utm_source=share"
        assert _clean_url(url) == "https://linkedin.com/posts/test-123"

    def test_clean_url_http_to_https(self):
        url = "http://linkedin.com/posts/test-123"
        assert _clean_url(url).startswith("https://")

    def test_is_post_url_posts(self):
        assert _is_post_url("https://www.linkedin.com/posts/john-doe-123")
        assert _is_post_url("https://linkedin.com/feed/update/urn:li:activity:123")

    def test_is_post_url_non_post(self):
        assert not _is_post_url("https://linkedin.com/in/john-doe")
        assert not _is_post_url("https://google.com")

    def test_parse_activity_urn(self):
        url = "https://linkedin.com/posts/test-activity-7312345678901234567-xx"
        urn = _parse_activity_urn(url)
        assert urn == "urn:li:activity:7312345678901234567"

    def test_parse_activity_urn_no_match(self):
        assert _parse_activity_urn("https://linkedin.com/in/test") == ""

    def test_parse_profile_id(self):
        assert _parse_profile_id("https://linkedin.com/in/john-doe") == "john-doe"
        assert _parse_profile_id("https://linkedin.com/in/john-doe?ref=x") == "john-doe"

    def test_parse_profile_id_no_match(self):
        assert _parse_profile_id("https://google.com") == ""


# ---------------------------------------------------------------------------
# JSON-LD extraction
# ---------------------------------------------------------------------------
class TestJsonLdExtraction:
    def test_extract_jsonld_direct(self):
        result = _extract_jsonld(SAMPLE_HTML)
        assert result is not None
        assert result["@type"] == "SocialMediaPosting"
        assert "AI is transforming" in result["articleBody"]

    def test_extract_jsonld_graph_wrapper(self):
        wrapped = {"@graph": [SAMPLE_JSONLD]}
        html = f'<script type="application/ld+json">{json.dumps(wrapped)}</script>'
        result = _extract_jsonld(html)
        assert result is not None
        assert result["@type"] == "SocialMediaPosting"

    def test_extract_jsonld_array(self):
        arr = [{"@type": "WebPage"}, SAMPLE_JSONLD]
        html = f'<script type="application/ld+json">{json.dumps(arr)}</script>'
        result = _extract_jsonld(html)
        assert result is not None
        assert result["@type"] == "SocialMediaPosting"

    def test_extract_jsonld_no_match(self):
        html = '<html><body>No JSON-LD here</body></html>'
        assert _extract_jsonld(html) is None

    def test_extract_jsonld_invalid_json(self):
        html = '<script type="application/ld+json">{invalid json}</script>'
        assert _extract_jsonld(html) is None

    def test_extract_jsonld_wrong_type(self):
        data = {"@type": "WebPage", "name": "Test"}
        html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        assert _extract_jsonld(html) is None

    def test_extract_jsonld_unquoted_type(self):
        """LinkedIn may omit quotes around the type attribute value."""
        html = f'<script type=application/ld+json>{json.dumps(SAMPLE_JSONLD)}</script>'
        result = _extract_jsonld(html)
        assert result is not None
        assert result["@type"] == "SocialMediaPosting"

    def test_extract_jsonld_with_nonce(self):
        """Script tag with extra attributes before type."""
        html = f'<script nonce="abc123" type="application/ld+json">{json.dumps(SAMPLE_JSONLD)}</script>'
        result = _extract_jsonld(html)
        assert result is not None
        assert result["@type"] == "SocialMediaPosting"


# ---------------------------------------------------------------------------
# Interaction counts
# ---------------------------------------------------------------------------
class TestInteractionCounts:
    def test_like_count(self):
        assert _get_interaction_count(SAMPLE_JSONLD, "LikeAction") == 142

    def test_comment_count(self):
        assert _get_interaction_count(SAMPLE_JSONLD, "CommentAction") == 23

    def test_share_count(self):
        assert _get_interaction_count(SAMPLE_JSONLD, "ShareAction") == 8

    def test_missing_action(self):
        assert _get_interaction_count(SAMPLE_JSONLD, "FollowAction") == 0

    def test_empty_stats(self):
        assert _get_interaction_count({}, "LikeAction") == 0

    def test_string_interaction_type(self):
        data = {
            "interactionStatistic": [
                {"interactionType": "LikeAction", "userInteractionCount": 50}
            ]
        }
        assert _get_interaction_count(data, "LikeAction") == 50

    def test_float_interaction_count(self):
        data = {
            "interactionStatistic": [
                {"interactionType": "LikeAction", "userInteractionCount": "142.0"}
            ]
        }
        assert _get_interaction_count(data, "LikeAction") == 142


# ---------------------------------------------------------------------------
# Comment extraction
# ---------------------------------------------------------------------------
class TestCommentExtraction:
    def test_extract_comments(self):
        comments = _extract_comments(SAMPLE_JSONLD)
        assert len(comments) == 2
        assert comments[0]["authorName"] == "Jane Smith"
        assert comments[0]["authorProfileUrl"] == "https://www.linkedin.com/in/jane-smith"
        assert "Great insight" in comments[0]["text"]
        assert comments[0]["postedAt"] == "2026-04-02T08:15:00Z"

    def test_extract_comments_empty(self):
        assert _extract_comments({}) == []
        assert _extract_comments({"comment": []}) == []

    def test_extract_comments_no_name_skipped(self):
        data = {"comment": [{"author": {"name": ""}, "text": "Hello"}]}
        assert _extract_comments(data) == []

    def test_extract_comments_non_list(self):
        data = {
            "comment": {
                "author": {"name": "Solo", "url": ""},
                "text": "Only comment",
                "datePublished": "2026-01-01",
            }
        }
        comments = _extract_comments(data)
        assert len(comments) == 1
        assert comments[0]["authorName"] == "Solo"


# ---------------------------------------------------------------------------
# Post normalisation
# ---------------------------------------------------------------------------
class TestNormalisePost:
    def test_normalise_full_post(self):
        post = _normalise_post(SAMPLE_JSONLD, source_url=SAMPLE_URL, keyword="AI")
        assert post["authorName"] == "John Doe"
        assert post["authorHeadline"] == "CEO at TechCorp"
        assert post["authorProfileId"] == "john-doe"
        assert post["numLikes"] == 142
        assert post["numComments"] == 23
        assert post["numShares"] == 8
        assert post["keyword"] == "AI"
        assert "urn:li:activity:7312345678901234567" == post["urn"]
        assert len(post["comments"]) == 2
        assert "scrapedAt" in post
        assert post["postedAt"] == "2026-04-01T10:00:00Z"

    def test_normalise_no_comments_flag(self):
        post = _normalise_post(SAMPLE_JSONLD, source_url=SAMPLE_URL, include_comments=False)
        assert "comments" not in post

    def test_normalise_minimal_data(self):
        minimal = {
            "@type": "SocialMediaPosting",
            "articleBody": "Short text but enough to pass the filter.",
        }
        post = _normalise_post(minimal, source_url="https://linkedin.com/posts/test")
        assert post["authorName"] == ""
        assert post["numLikes"] == 0
        assert post["comments"] == []
        assert post["text"] == "Short text but enough to pass the filter."

    def test_normalise_author_as_list(self):
        data = {
            **SAMPLE_JSONLD,
            "author": [SAMPLE_JSONLD["author"]],
        }
        post = _normalise_post(data, source_url=SAMPLE_URL)
        assert post["authorName"] == "John Doe"

    def test_no_duplicate_fields(self):
        """Verify the output has no legacy alias fields."""
        post = _normalise_post(SAMPLE_JSONLD, source_url=SAMPLE_URL)
        # These old aliases should NOT exist
        assert "naam" not in post
        assert "persoon" not in post
        assert "functie" not in post
        assert "zoekterm" not in post
        assert "bron" not in post
        assert "post_url" not in post
        assert "snippet" not in post
        assert "likes" not in post
        assert "datum" not in post

    def test_url_cleaned(self):
        url_with_params = SAMPLE_URL + "?utm_source=share&ref=123"
        post = _normalise_post(SAMPLE_JSONLD, source_url=url_with_params)
        assert "utm_source" not in post["postUrl"]
        assert "ref=" not in post["postUrl"]


# ---------------------------------------------------------------------------
# Scrape post (with mock HTTP)
# ---------------------------------------------------------------------------
class TestScrapePost:
    @patch("src.scraper._fetch_html")
    def test_scrape_post_success(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_HTML
        post = scrape_post(SAMPLE_URL, keyword="AI")
        assert post is not None
        assert post["authorName"] == "John Doe"
        assert post["keyword"] == "AI"
        assert len(post["comments"]) == 2

    @patch("src.scraper._fetch_html")
    def test_scrape_post_no_html(self, mock_fetch):
        mock_fetch.return_value = None
        assert scrape_post(SAMPLE_URL) is None

    @patch("src.scraper._fetch_html")
    def test_scrape_post_no_jsonld(self, mock_fetch):
        mock_fetch.return_value = "<html><body>No data</body></html>"
        assert scrape_post(SAMPLE_URL) is None

    @patch("src.scraper._fetch_html")
    def test_scrape_post_text_too_short(self, mock_fetch):
        short_data = {**SAMPLE_JSONLD, "articleBody": "Hi"}
        html = f'<script type="application/ld+json">{json.dumps(short_data)}</script>'
        mock_fetch.return_value = html
        assert scrape_post(SAMPLE_URL) is None

    @patch("src.scraper._fetch_html")
    def test_scrape_post_without_comments(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_HTML
        post = scrape_post(SAMPLE_URL, include_comments=False)
        assert post is not None
        assert "comments" not in post

    @patch("src.scraper._fetch_html")
    def test_scrape_post_og_fallback(self, mock_fetch):
        """When JSON-LD has no engagement stats, OG meta tags should fill in."""
        no_stats = {**SAMPLE_JSONLD, "interactionStatistic": []}
        html = (
            '<html><head>'
            '<meta property="og:description" content="87 likes, 12 comments">'
            '<script type="application/ld+json">' + json.dumps(no_stats) + '</script>'
            '</head></html>'
        )
        mock_fetch.return_value = html
        post = scrape_post(SAMPLE_URL, keyword="test")
        assert post is not None
        assert post["numLikes"] == 87
        assert post["numComments"] == 12


# ---------------------------------------------------------------------------
# OG engagement fallback
# ---------------------------------------------------------------------------
class TestOgEngagement:
    def test_og_with_likes_and_comments(self):
        html = '<meta property="og:description" content="142 likes, 23 comments, 8 reposts">'
        result = _extract_og_engagement(html)
        assert result["likes"] == 142
        assert result["comments"] == 23
        assert result["shares"] == 8

    def test_og_with_reactions(self):
        html = '<meta property="og:description" content="1,234 reactions">'
        result = _extract_og_engagement(html)
        assert result["likes"] == 1234

    def test_og_no_match(self):
        html = '<meta property="og:description" content="Check out this post">'
        result = _extract_og_engagement(html)
        assert result == {}

    def test_og_no_meta_tag(self):
        html = '<html><body>Nothing here</body></html>'
        result = _extract_og_engagement(html)
        assert result == {}


# ---------------------------------------------------------------------------
# HTTP fetch with retry
# ---------------------------------------------------------------------------
def _mock_session(mock_get):
    """Helper: reset the module-level session and inject a mock."""
    mock_sess = MagicMock()
    mock_sess.get = mock_get
    _scraper_mod._session = mock_sess
    return mock_sess


class TestFetchHtml:
    def setup_method(self):
        _scraper_mod._session = None
        _scraper_mod._rate_limit_hits = 0

    def teardown_method(self):
        _scraper_mod._session = None
        _scraper_mod._rate_limit_hits = 0

    def test_fetch_success(self):
        mock_get = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_HTML
        mock_resp.url = SAMPLE_URL
        mock_get.return_value = mock_resp
        _mock_session(mock_get)

        result = _fetch_html(SAMPLE_URL)
        assert result == SAMPLE_HTML

    def test_fetch_404(self):
        mock_get = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.url = SAMPLE_URL
        mock_get.return_value = mock_resp
        _mock_session(mock_get)

        assert _fetch_html(SAMPLE_URL) is None

    @patch("src.scraper.time.sleep")
    def test_fetch_429_retry(self, mock_sleep):
        mock_get = MagicMock()
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.url = SAMPLE_URL

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.text = SAMPLE_HTML
        resp_200.url = SAMPLE_URL

        mock_get.side_effect = [resp_429, resp_200]
        _mock_session(mock_get)

        result = _fetch_html(SAMPLE_URL)
        assert result == SAMPLE_HTML
        assert mock_get.call_count == 2

    @patch("src.scraper.time.sleep")
    def test_fetch_all_retries_exhausted(self, mock_sleep):
        mock_get = MagicMock()
        resp_500 = MagicMock()
        resp_500.status_code = 500
        resp_500.url = SAMPLE_URL
        mock_get.return_value = resp_500
        _mock_session(mock_get)

        assert _fetch_html(SAMPLE_URL) is None
        assert mock_get.call_count == 3  # MAX_RETRIES

    def test_fetch_auth_wall(self):
        mock_get = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>Please log in</html>"
        mock_resp.url = "https://www.linkedin.com/authwall?trk=123"
        mock_get.return_value = mock_resp
        _mock_session(mock_get)

        assert _fetch_html(SAMPLE_URL) is None

    def test_fetch_with_proxy(self):
        mock_get = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_HTML
        mock_resp.url = SAMPLE_URL
        mock_get.return_value = mock_resp
        _mock_session(mock_get)

        proxies = {"http": "http://proxy:8000", "https": "http://proxy:8000"}
        _fetch_html(SAMPLE_URL, proxies=proxies)

        _, kwargs = mock_get.call_args
        assert kwargs["proxies"] == proxies

    def test_fetch_redirect_to_non_linkedin(self):
        """Redirects to non-LinkedIn domains should be blocked."""
        mock_get = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>evil</html>"
        mock_resp.url = "https://evil.com/capture"
        mock_get.return_value = mock_resp
        _mock_session(mock_get)

        assert _fetch_html(SAMPLE_URL) is None

    def test_fetch_redirect_signin(self):
        """LinkedIn /signin redirect should be treated as auth wall."""
        mock_get = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>Sign in</html>"
        mock_resp.url = "https://www.linkedin.com/signin?trk=123"
        mock_get.return_value = mock_resp
        _mock_session(mock_get)

        assert _fetch_html(SAMPLE_URL) is None


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
class TestDedup:
    def test_dedup_by_urn(self):
        posts = [
            {"urn": "urn:1", "postUrl": "a", "text": "Post 1"},
            {"urn": "urn:1", "postUrl": "b", "text": "Post 1 dupe"},
            {"urn": "urn:2", "postUrl": "c", "text": "Post 2"},
        ]
        result = _dedup(posts)
        assert len(result) == 2

    def test_dedup_by_url(self):
        posts = [
            {"urn": "", "postUrl": "https://linkedin.com/posts/same", "text": "A"},
            {"urn": "", "postUrl": "https://linkedin.com/posts/same", "text": "B"},
        ]
        assert len(_dedup(posts)) == 1

    def test_dedup_preserves_order(self):
        posts = [
            {"urn": "urn:1", "text": "First"},
            {"urn": "urn:2", "text": "Second"},
            {"urn": "urn:1", "text": "First dupe"},
        ]
        result = _dedup(posts)
        assert result[0]["text"] == "First"
        assert result[1]["text"] == "Second"


# ---------------------------------------------------------------------------
# Date filter mapping
# ---------------------------------------------------------------------------
class TestDateFilterMap:
    def test_all_filters_mapped(self):
        assert _DATE_FILTER_MAP["past-day"] == "d"
        assert _DATE_FILTER_MAP["past-24h"] == "d"
        assert _DATE_FILTER_MAP["past-week"] == "w"
        assert _DATE_FILTER_MAP["past-month"] == "m"


# ---------------------------------------------------------------------------
# URL validation (SSRF prevention)
# ---------------------------------------------------------------------------
class TestUrlValidation:
    def test_valid_linkedin_urls(self):
        assert validate_linkedin_url("https://www.linkedin.com/posts/test-123")
        assert validate_linkedin_url("https://linkedin.com/posts/test-123")
        assert validate_linkedin_url("https://nl.linkedin.com/posts/test-123")
        assert validate_linkedin_url("https://de.linkedin.com/feed/update/urn:li:activity:123")

    def test_rejects_non_linkedin(self):
        assert not validate_linkedin_url("https://google.com")
        assert not validate_linkedin_url("https://evil.com/linkedin.com/posts/")
        assert not validate_linkedin_url("http://localhost:8080")
        assert not validate_linkedin_url("http://169.254.169.254/latest/meta-data/")
        assert not validate_linkedin_url("file:///etc/passwd")
        assert not validate_linkedin_url("https://evillinkedin.com/posts/test")
        assert not validate_linkedin_url("https://notlinkedin.com/posts/test")

    def test_rejects_malformed(self):
        assert not validate_linkedin_url("")
        assert not validate_linkedin_url("not-a-url")


# ---------------------------------------------------------------------------
# Dedup edge cases
# ---------------------------------------------------------------------------
class TestDedupEdgeCases:
    def test_dedup_no_identifier_kept(self):
        """Posts without URN or URL should not be dropped."""
        posts = [
            {"urn": "", "postUrl": "", "text": "No ID post"},
            {"urn": "", "postUrl": "", "text": "Another no ID post"},
        ]
        result = _dedup(posts)
        assert len(result) == 2

    def test_dedup_empty_list(self):
        assert _dedup([]) == []


# ---------------------------------------------------------------------------
# Retry logic (fixed: last attempt not wasted)
# ---------------------------------------------------------------------------
class TestRetryFix:
    def setup_method(self):
        _scraper_mod._session = None
        _scraper_mod._rate_limit_hits = 0

    def teardown_method(self):
        _scraper_mod._session = None
        _scraper_mod._rate_limit_hits = 0

    @patch("src.scraper.time.sleep")
    def test_retry_returns_none_after_all_500s(self, mock_sleep):
        """After MAX_RETRIES 500s, should return None without extra sleep."""
        mock_get = MagicMock()
        resp_500 = MagicMock()
        resp_500.status_code = 500
        resp_500.url = SAMPLE_URL
        mock_get.return_value = resp_500
        _mock_session(mock_get)

        result = _fetch_html(SAMPLE_URL)
        assert result is None
        assert mock_get.call_count == 3  # 3 actual attempts
        # Should sleep only between retries (2 sleeps for 3 attempts)
        assert mock_sleep.call_count == 2


# ---------------------------------------------------------------------------
# Rate-limit threshold
# ---------------------------------------------------------------------------
class TestRateLimitThreshold:
    def setup_method(self):
        _scraper_mod._session = None
        _scraper_mod._rate_limit_hits = 0

    def teardown_method(self):
        _scraper_mod._session = None
        _scraper_mod._rate_limit_hits = 0

    @patch("src.scraper.time.sleep")
    def test_skips_after_consecutive_429s(self, mock_sleep):
        """After 5 consecutive 429s, should skip without even trying."""
        mock_get = MagicMock()
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.url = SAMPLE_URL
        mock_get.return_value = resp_429
        _mock_session(mock_get)

        # Exhaust the threshold (5 consecutive 429s across multiple calls)
        for _ in range(3):
            _fetch_html(SAMPLE_URL)

        # Counter should be high enough to skip
        assert _scraper_mod._rate_limit_hits >= 5

        mock_get.reset_mock()
        result = _fetch_html(SAMPLE_URL)
        assert result is None
        assert mock_get.call_count == 0  # didn't even try

    def test_rate_limit_resets_on_success(self):
        """A successful request should reset the 429 counter."""
        _scraper_mod._rate_limit_hits = 4  # almost at threshold

        mock_get = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = SAMPLE_HTML
        mock_resp.url = SAMPLE_URL
        mock_get.return_value = mock_resp
        _mock_session(mock_get)

        result = _fetch_html(SAMPLE_URL)
        assert result == SAMPLE_HTML
        assert _scraper_mod._rate_limit_hits == 0


# ---------------------------------------------------------------------------
# Google CSE discovery
# ---------------------------------------------------------------------------
class TestGoogleCSE:
    def test_no_keys_returns_empty(self):
        """Without API key and CSE ID, should return empty list."""
        result = find_urls_google_cse("AI automation", api_key="", cse_id="")
        assert result == []

    @patch("src.discovery.requests.get")
    def test_google_cse_parses_results(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {"link": "https://www.linkedin.com/posts/john-doe-test-123"},
                {"link": "https://www.linkedin.com/in/someone"},  # not a post
                {"link": "https://nl.linkedin.com/posts/another-post-456"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = find_urls_google_cse("AI", api_key="test-key", cse_id="test-cx")
        assert len(result) == 2
        assert "linkedin.com/posts/john-doe-test-123" in result[0]
        assert "linkedin.com/posts/another-post-456" in result[1]

    @patch("src.discovery.requests.get")
    def test_google_cse_handles_rate_limit(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        result = find_urls_google_cse("AI", api_key="key", cse_id="cx")
        assert result == []

    @patch("src.discovery.requests.get")
    def test_google_cse_handles_quota_exceeded(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_get.return_value = mock_resp

        result = find_urls_google_cse("AI", api_key="key", cse_id="cx")
        assert result == []
