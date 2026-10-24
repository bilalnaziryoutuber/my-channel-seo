# My Channel SEO

This repository runs a daily, read-only YouTube metadata sync for
`@hassanparachaoffical`, refreshes SEO metadata with Gemini, generates a static
site, commits its database state, and deploys it to GitHub Pages.

## Pipeline

1. yt-dlp lists the complete uploads playlist without an item cap. The optional
   legacy YouTube Data API helpers remain in the code but are not required or
   used by the normal sync.
2. Videos missing from a successful listing accrue a `missing_count`; they are
   marked removed only after three misses. Removal updates are skipped when
   fewer than 90% of known active videos appear in the listing.
3. A rolling slice (50 videos by default) is refreshed with yt-dlp, oldest
   `last_synced` first. Detail refreshes stop on the configured time budget or a
   YouTube bot-check response. The job does not use or store cookies.
4. A daily queue prioritizes videos without SEO, then stale SEO, refreshing up
   to 35 records per run. Gemini receives source metadata—not previous SEO—and
   successful refreshes rotate the old SEO object into a three-entry history.
   YouTube autocomplete suggestions are optional best-effort keyword ideas.
5. `site/` contains listing/topic pages and `/v/<id>/` pages for videos with SEO,
   plus a sitemap and robots.txt. Video embeds load only after a click.
6. GitHub Actions commits `video_db.json` and deploys the site. Workflow
   dispatch supports `sync_slice_size`, `refresh_per_day`, and `dry_run`.

## Video database

Each record keeps `original_title` separate from generated SEO:

- `seo`: title, description, keywords, prompt version, update timestamp, model,
  and source tags.
- `seo_history`: up to three prior SEO objects.
- YouTube metadata: `last_synced`, `published_at` (date or `null`),
  `duration_seconds`, `view_count`, `tags`, and the YouTube `description`.
- Sync state: `status` (`active` or `removed`) and `missing_count`.

Legacy polished titles migrate to `seo.title` at prompt version 0, retaining
their original title and `polished_at` as the SEO update time. Legacy records
without a polished title receive no SEO until refreshed.

## Configuration

Required repository secret:

- `GEMINI_API_KEY` — Gemini API key. When unset, sync and site generation
  continue while SEO refresh is skipped.

YouTube API credentials are not required. Do not provide or store YouTube
cookies.

Environment variables:

- `GEMINI_MODELS` — ordered model candidates; defaults to
  `gemini-3.5-flash-lite,gemini-3.6-flash,gemini-3.5-flash`.
- `GEMINI_DAILY_REQUEST_LIMIT` — per-model request cap, default `18`.
- `SYNC_SLICE_SIZE` — details per run, default `50`.
- `SYNC_TIME_BUDGET_MIN` — detail-sync time budget, default `20`.
- `REFRESH_PER_DAY` — SEO records per run, default `35`.
- `MIN_REFRESH_DAYS` — minimum age for ordinary refreshes, default `21`.
- `SEO_USE_SEARCH_GROUNDING=1` — opt in to Gemini Google Search grounding.
- `SITE_BASE_URL` — absolute site URL used by canonical tags and sitemap.
- `DRY_RUN=1` — skip YouTube, autocomplete, Gemini, and database writes while
  building the site from a migrated in-memory copy of `video_db.json`.

## Run locally

Use Python 3.12 or newer:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest
DRY_RUN=1 python engine.py
```

The scheduled workflow runs at 19:00 UTC. You can also start it manually from
Actions and set the rolling-slice, refresh-limit, and dry-run inputs.
