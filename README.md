# RoleRadar — every opportunity, one search

RoleRadar turns the scattered internet job market into a single, fast search engine.
It continuously crawls public job APIs, company career-page boards, RSS feeds and
community hiring threads; normalizes every listing into a rich, structured record;
deduplicates cross-posted roles; detects stale/expired postings; and serves the
whole thing through one instant search UI.

**6,800+ live opportunities from 1,280+ companies** in the initial crawl, refreshing
automatically every 45 minutes.

## Run it

```bash
cd roleradar
python3 server.py          # http://localhost:8000  (no dependencies — Python 3.11+ stdlib only)
```

On first start the crawler runs automatically in the background (~40s); the UI shows
sync progress in real time. A re-crawl then runs every 45 minutes, or on demand with
the **Sync** button / `POST /api/refresh`.

Optional tuning via environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | HTTP port |
| `ROLERADAR_REFRESH_MINUTES` | `45` | background re-crawl interval |
| `ROLERADAR_STALE_DAYS` | `75` | age after which a posting is flagged outdated |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | unset | enables the Adzuna source |
| `JOOBLE_API_KEY` | unset | enables the Jooble source |
| `THEMUSE_API_KEY` | unset | enables The Muse source |

## Sources

**Aggregators & feeds (no key required)**
RemoteOK · Remotive · Arbeitnow (EU + visa sponsorship flags) · Jobicy ·
We Work Remotely (RSS, 5 categories) · Hacker News “Ask HN: Who is hiring?”
(live monthly thread, parsed into structured posts) · SimplifyJobs GitHub lists
(Summer internships, off-season internships, new-grad roles — ~2,500 rows parsed
from the community tables).

**Company career pages (direct ATS board APIs — the same postings LinkedIn/Indeed syndicate)**
Greenhouse: Stripe, Coinbase, Figma, Datadog, Databricks, Cloudflare
Ashby: OpenAI, Perplexity, Notion, Ramp, Cohere
Lever: adapter included — drop company slugs into `config.LEVER_COMPANIES`.

**Optional keyed integrations** — Adzuna, Jooble, The Muse activate automatically
when their env keys are present. Extra RSS/Atom feeds can be added in
`config.EXTRA_RSS_FEEDS`.

> **About LinkedIn & Indeed:** neither offers a public API and both block
> server-side scraping. RoleRadar ingests the *same underlying postings* directly
> from company ATS boards (Greenhouse/Ashby/Lever), which is how those platforms
> receive them anyway. The `/api/sources` endpoint lists enabled sources plus
> everything available-but-unconfigured.

## What every opportunity is normalized into

full JD (rich HTML + plain text) · responsibilities/requirements · skills/tags
(auto-extracted) · location · remote/hybrid/on-site · type (full-time, part-time,
contract, internship, freelance, apprenticeship, research, gig) · experience level ·
salary (min/max/currency/period + ±USD estimate, parsing `$120k-$150k`, `₹8–12 LPA`,
`€65.000–85.000`, stipends, hourly rates…) · posted date (absolute or relative like
“3 days ago”) · application deadline (scraped from “apply by …” language) ·
company · apply URL · original source posting(s).

## The differentiators

- **Cross-source deduplication** — canonical fingerprinting
  (normalized company + title + location), apply-URL matching with tracking-param
  stripping, and a fuzzy title-similarity pass within companies. Cards show
  “seen on N sources” with every original posting linked.
- **Expiry detection** — deadline passed / posting older than N days / delisted by
  its source ⇒ flagged `expired`, hidden by default, one toggle to include.
- **One search engine UX** — full-text search (SQLite FTS5, prefix + phrase +
  `-minus` exclusion), faceted filters with live counts, salary-floor and freshness
  filters, trending-skill chips, relevance/newest/pay/deadline sorting,
  shareable search URLs, local saved/bookmarked jobs, infinite scroll.
- **Transparent provenance** — `/api/sources` reports per-source fetch counts,
  last run, and errors after every crawl.

## API

| Endpoint | Description |
|---|---|
| `GET /api/jobs` | search. Params: `q`, `type`, `remote`, `level`, `source`, `posted` (24h/3d/7d/14d/30d), `min_salary`, `has_salary`, `include_expired`, `sort` (newest/relevance/salary/deadline), `page`, `per_page`, `tag`, `company` |
| `GET /api/jobs/{id}` | one opportunity, full JD + all source postings |
| `GET /api/stats` | totals, per-type/platform counts, source run health, top tags |
| `GET /api/sources` | enabled sources + available-but-unconfigured integrations |
| `GET /api/sync` | live sync progress |
| `POST /api/refresh` | trigger a full re-crawl |

Example: `/api/jobs?q="machine+learning"+intern+-senior&type=internship&remote=remote&posted=7d&sort=newest`

## Architecture

```
roleradar/
├── server.py               # stdlib HTTP server: JSON API + static UI
├── aggregator/
│   ├── config.py           # sources, boards, timing, FX
│   ├── util.py             # polite HTTP fetcher, HTML→text, date/deadline parsing
│   ├── salary.py           # compensation extraction & normalization (12+ currencies)
│   ├── classify.py         # type / level / remote-mode / skill inference
│   ├── models.py           # raw listing → normalized job
│   ├── dedupe.py           # fingerprints, URL matching, fuzzy merge, record richness
│   ├── db.py               # SQLite + WAL + FTS5 search w/ facets, expiry sweeps
│   ├── pipeline.py         # concurrent fetch → normalize → dedupe → atomic rebuild
│   └── sources/            # one adapter per source (aggregators, boards, keyed APIs)
├── static/                 # dependency-free SPA (dark UI)
└── data/roleradar.db       # created on first run
```

Reliability choices: **zero third-party dependencies** (pure Python stdlib), polite
per-host rate limiting with timeouts/retries, WAL-mode SQLite, atomic rebuilds so the
UI never sees a half-written index, and per-source failure isolation (one dead source
never blocks a sync).

Adding a source = subclass `Source` in `aggregator/sources/`, implement `fetch()`
returning raw listings, register it in `sources/__init__.py`. The normalizer,
deduper, salary/date extractors and search index handle the rest automatically.
