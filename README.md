# Competitor Monitor

Enter a competitor's domain. The tool finds their blog posts, flags what's new since the last check,
looks up keyword + search volume, tracks pricing page changes, and sends a Slack alert.

**Live demo:** <your-hosted-link>  |  **Loom:** <your-loom-link>

> Slack alerts, keyword volume and the database path are configured with environment variables (see `.env.example`). No secrets are stored in this repo.

## How it works
1. **Scrape (own scraper, no scraping API):** `requests` + `BeautifulSoup`. Discovery order:
   sitemap (`robots.txt` -> `/sitemap.xml` and common variants, incl. nested/gzipped) -> RSS/Atom feeds ->
   crawling blog listing pages. Respects `robots.txt` for page fetches.
2. **Detect new content:** every post URL is normalized (no query/fragment/trailing slash) and stored in
   SQLite with title, publish date, meta description, keyword, volume, first_seen.
   A post is "new" only if its URL isn't already stored, so a second run on the same domain reports nothing
   unless something was published.
3. **Pricing changes:** fetches `/pricing` (or `/plans`), keeps the price-related lines, hashes them, and diffs
   against the last snapshot.
4. **Keyword enrichment:** target keyword guessed from meta keywords > H1 > title (heuristic).
   Volume from an Apify keyword-volume actor (`APIFY_TOKEN`), Keywords Everywhere, or DataForSEO (set one in env).
   Works without a key (shows n/a with the reason).
5. **Alert:** Slack incoming webhook: "domain published N new posts since the last check" with titles, URLs,
   keyword + volume, and any pricing change.

## Failure handling
| Case | Behavior |
|---|---|
| No blog found | `no_blog` status with a clear message |
| 403/429/bot-check page | `blocked` status, nothing scraped, no crash |
| Invalid / exhausted API key | posts still saved and alerted, volume shown as n/a with the reason |
| Site unreachable / timeout | `unreachable` status |
| Slack webhook missing/failing | reported in the UI, tool keeps working |
| Private/internal addresses | rejected (SSRF guard) |

## Run locally
```
pip install -r requirements.txt
cp .env.example .env   # fill in what you have, then: export $(cat .env | xargs)
python app.py          # http://localhost:5000
python test_local.py   # offline tests against mock sites
```

## Deploy (Render)
New Web Service from this repo. Build: `pip install -r requirements.txt`. Start: `gunicorn app:app --timeout 180 --workers 1`.
Add env vars from `.env.example`. Note: Render's free disk is ephemeral, so saved data resets on redeploy/sleep.
Attach a small persistent disk and set `DB_PATH` to it if you need data to survive.

## Known limits
- JavaScript-only blogs with no sitemap/RSS can't be read (no headless browser).
- Keyword extraction is a heuristic, not true keyword research.
- First scan details only the 30 most recent posts; older URLs are stored as already seen.
- Runs on demand; schedule it weekly with any cron hitting `POST /api/check` (`{"domain": "..."}`).
