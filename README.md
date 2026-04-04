# LinkedIn Post & Comment Scraper

Scrape LinkedIn posts **and their comments** by keyword or direct URL. No browser, no cookie, no login required.

## What does this actor do?

This actor finds and scrapes public LinkedIn posts using two methods:

1. **Keyword discovery** — searches DuckDuckGo and optionally Google Custom Search for LinkedIn post URLs matching your keywords, then extracts full post data + comments.
2. **Direct URL scraping** — provide specific LinkedIn post URLs to scrape.

For each post, the actor extracts:
- Full post text, author name, headline, and profile URL
- Engagement metrics (likes, comments, shares)
- **Up to ~10 comments** with commenter name, text, and date

All data comes from LinkedIn's public JSON-LD metadata — no authentication needed.

## Why this actor?

| Feature | This actor | Typical LinkedIn scrapers |
|---------|-----------|--------------------------|
| Cookie required | No | Yes |
| Browser required | No | Yes (Playwright/Puppeteer) |
| Keyword discovery | Yes (DDG + Google CSE) | No (URL input only) |
| Comments included | Yes (up to ~10/post) | Often separate actor |
| Memory usage | ~512 MB | 1-4 GB (browser) |
| Speed | ~1s per post | ~5-10s per post |
| Proxy rotation | Per-request IP rotation | Often static |

## Input

### Keyword mode (recommended)
```json
{
  "keywords": ["AI automation", "digital transformation SME"],
  "maxPostsPerKeyword": 15,
  "dateFilter": "past-week",
  "scrapeComments": true,
  "proxyConfiguration": { "useApifyProxy": true }
}
```

### Direct URL mode
```json
{
  "startUrls": [
    {"url": "https://www.linkedin.com/posts/example-post-123"}
  ],
  "scrapeComments": true
}
```

### Input parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `keywords` | string[] | — | Search terms for post discovery (max 20) |
| `startUrls` | object[] | — | Direct LinkedIn post URLs |
| `maxPostsPerKeyword` | integer | 15 | Max posts collected per keyword (1-25) |
| `dateFilter` | string | `past-week` | Time filter: `past-day`, `past-week`, `past-month` |
| `scrapeComments` | boolean | `true` | Extract comments from each post |
| `proxyConfiguration` | object | — | **Recommended** — Apify proxy for reliable results |

## Output

Each post in the dataset contains:

```json
{
  "urn": "urn:li:activity:7312345678901234567",
  "postUrl": "https://www.linkedin.com/posts/john-doe-example-123",
  "text": "Full post text content...",
  "authorName": "John Doe",
  "authorHeadline": "CEO at Example Corp",
  "authorProfileUrl": "https://www.linkedin.com/in/john-doe",
  "authorProfileId": "john-doe",
  "authorImageUrl": "https://media.licdn.com/...",
  "numLikes": 142,
  "numComments": 23,
  "numShares": 8,
  "postedAt": "2026-04-01T12:00:00Z",
  "keyword": "AI automation",
  "scrapedAt": "2026-04-04T10:30:00Z",
  "comments": [
    {
      "authorName": "Jane Smith",
      "authorProfileUrl": "https://www.linkedin.com/in/jane-smith",
      "text": "Great insight! We implemented something similar...",
      "postedAt": "2026-04-02T08:15:00Z"
    }
  ]
}
```

## Proxy (recommended)

The actor works without proxy for small test runs (~5 posts). For production use, **enable Apify proxy** — the actor rotates IPs automatically per request to avoid LinkedIn rate limits.

Without proxy, LinkedIn will rate-limit after ~10 requests, and you'll get fewer results.

## Reliability features

- **Per-request proxy rotation** — each HTTP request uses a fresh proxy IP
- **Automatic rate-limit detection** — stops gracefully after consecutive 429s (no wasted compute)
- **Incremental data push** — results are pushed per keyword batch, so partial results are saved even if the run times out
- **Retry with exponential backoff** — transient errors (429, 5xx) are retried up to 3 times
- **Cross-keyword deduplication** — no duplicate posts when keywords overlap

## Limitations

- **Comments**: LinkedIn's public JSON-LD includes up to ~10 comments per post. Posts with more comments will only return the first ~10.
- **Private posts**: Posts from private/restricted accounts return no data (they require authentication).
- **Search volume**: DuckDuckGo typically returns 3-8 URLs per keyword. Adding Google Custom Search (free: 100 queries/day) significantly improves coverage.
- **No author headline**: JSON-LD sometimes omits the author's job title. This field may be empty.

## Tips for best results

1. **Enable proxy** — strongly recommended for any run with more than 5 posts.
2. **Use specific keywords** — "AI automation manufacturing" finds better posts than just "AI".
3. **Set `dateFilter` to `past-week`** — recent posts have the freshest engagement data.
4. **Add Google Custom Search** (optional, free) — set `GOOGLE_API_KEY` and `GOOGLE_CSE_ID` environment variables for 2-3x more URL results per keyword. Setup takes 5 minutes:
   - Get API key at [console.cloud.google.com](https://console.cloud.google.com/) (enable "Custom Search API")
   - Create search engine at [programmablesearchengine.google.com](https://programmablesearchengine.google.com/) with "Search the entire web" enabled
   - Free tier: 100 queries/day (enough for ~50 keywords with pagination)

## Cost

This actor uses only HTTP requests (no browser), so compute costs are minimal:
- ~512 MB memory
- ~1 second per post
- A typical run of 10 keywords × 15 posts ≈ $0.01-0.02 on Apify

## Local usage

```bash
pip install -r requirements.txt

# Keyword search
python3 -m src.main --keywords "AI automation" "digital transformation" --limit 10

# Direct URLs
python3 -m src.main --urls "https://linkedin.com/posts/..." --no-comments

# Output to custom path
python3 -m src.main --keywords "AI" --output results.json
```
