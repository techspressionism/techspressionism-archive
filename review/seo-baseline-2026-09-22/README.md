# SEO baseline — 2026-09-22, pre–Phase 2

Captured from Google Search Console (property: `https://techspressionism.com/`) the same day the
Video Archive's `<title>`/description SEO work was done, specifically so we have a documented
"before" state to compare against once the archive is redirected into techspressionism.com
(Phase 2). Re-run this capture after that launch and diff against this snapshot to see what the
redirect actually did to visibility. Logged in as colin@goldberg.art via the Browser pane, Colin's
own session — nothing here was pulled through a paid connector.

**Scope:** this covers the live techspressionism.com site as a whole (its current `/video/...`,
`/salon/`, `/artists/` pages etc.) — not the new archive site under construction, which isn't
indexed under its own domain yet. **Exception: see the sitemap finding below — worth Colin's own
look**, it suggests `/archive/` may already be wired up further than expected.

## ⚠️ Flag for Colin: an `/archive/sitemap.xml` is already submitted

Under Sitemaps, two nearly-identical submissions already exist, both dated **Sep 21, 2026 (yesterday)**:
- `techspressionism.com/archive/sitemap.xml` — 372 discovered pages, 148 videos, status Success
- `techspressionism.com/archive/sitemap.xml,` — 371 pages, 147 videos, status Success (note the
  trailing comma in the URL — this second one looks like an accidental duplicate submission, not
  a separate real sitemap)

Neither of us submitted this in today's session. Worth checking: is `/archive/` already partially
live on WP Engine, or was this submitted in advance of Phase 2 and the underlying pages 404 for
now? "Discovered" only means Google fetched and parsed the sitemap XML successfully — it doesn't
mean those pages are indexed (indexing status is tracked separately, see below) or even that they
resolve. Also worth cleaning up the duplicate (trailing-comma) submission either way.

## Headline numbers (trailing 3 months, Search type: Web)

- **284 total clicks, 30.2K total impressions, 0.9% average CTR, 9.9 average position**
  (Search Console's own report-level aggregates; the per-page/per-query CSV sums differ slightly —
  293 clicks / 35.8K impressions — a normal GSC aggregation quirk, not an error)
- By device: Desktop 181 clicks / 20,823 impr / 0.87% CTR / pos 11.2; Mobile 99 clicks / 9,112
  impr / 1.09% CTR / pos 7.1; Tablet 4 clicks / 227 impr.

## Indexing coverage (all-time, `indexing/` folder)

**282 indexed, 229 not indexed.** Breakdown of the 229:
| Reason | Source | Pages |
|---|---|---|
| Discovered – currently not indexed | Google systems | **152** |
| Crawled – currently not indexed | Google systems | **58** |
| Page with redirect | Website | 8 |
| Not found (404) | Website | 7 |
| Alternate page with proper canonical tag | Website | 2 |
| Blocked due to other 4xx issue | Website | 1 |
| Excluded by 'noindex' tag | Website | 1 |
| Blocked by robots.txt | Website | 0 |

The big ones — 210 of 229 — are Google's own crawl/quality decisions, not broken links: 152 pages
Google knows about but hasn't gotten to yet (crawl budget/priority), and 58 it crawled but chose
not to index (usually a duplicate/thin-content judgment). **This is the single most relevant
finding for Phase 2**: when the archive adds several hundred more pages at once, a lot of them
could land in this same "discovered but not indexed" limbo unless internal linking and the
sitemap actively signal priority. Worth planning around — e.g. staged rollout, strong internal
linking from already-indexed high-traffic pages (home, `/salon/`, `/artists/`) into the new pages,
not just relying on the sitemap alone.

## Backlinks (Links report)

- **475 external links** from other sites, **4,303 internal links**.
- Top external-linking sites: goldberg.art (192 links, 11 target pages — Colin's own site),
  southamptonartscenter.org (19 links, 19 distinct target pages — likely one link per artist),
  kunstmatrix.com (14), linktr.ee (11), **wikipedia.org (11 links, 7 target pages)** — genuine
  Wikipedia backlinks, valuable domain-authority signal, worth knowing which 7 pages those point
  to since they're your best-linked assets. Also roynicholson.com (10), kickstarter.com (9),
  theartleague.org (9), grokipedia.com (7), rozdimon.com (7).
- Top externally-linked pages: `/brooklyn/` (189 links — by far the most-linked page on the site),
  home (142), `/artists/` (23), `/salon/` (16), `/chelsea/` (9).
- Top internally-linked pages: home, `/author/everbeta/`, `/artists/`, a PDF upload
  (`2_Prompt-magazine-issue-15-1.pdf`), `/interviews/`, `/roundtable/`, `/salon/`, `/exhibitions/`,
  `/join/`, `/manifesto/`.

## 28-day Insights (Google's own trending view)

- 77 clicks (↓11%), 9,280 impressions (↑8%) over the last 28 days.
- Trending content: `/video/interview/sasha-stiles/` and `/video/interview/rees/` both **+100%**
  clicks; `/artists/` +50%; home −29%.
- Trending queries: **"sasha stiles" +400%** (10 clicks, up from ~2) — a genuinely hot query right
  now, good timing for the title-tag fix. "annette weintraub", "artist loops", "bernard bousquet"
  all newly appearing this period (previously 0).

## Files

- `Queries.csv` (600 rows), `Pages.csv` (287 rows), `Countries.csv`, `Devices.csv`,
  `Search appearance.csv`, `Chart.csv`, `Filters.csv` — full 3-month Performance export.
- `indexing/` — Page-indexing coverage export (`Critical issues.csv`, `Chart.csv`, `Metadata.csv`).
- Links report has no clean CSV export captured (the browser download didn't trigger); the numbers
  above are transcribed directly from the UI and are current as of this date.

## Headline finding that drove today's title-tag work

Many individual artist-name queries already rank at position 1–3 (Google already picks the right
page) but get 0% or near-0% CTR — a snippet/title problem, not a visibility problem. Examples:
Steve Miller (pos 1.1, 73 impr, 0 clicks), Patrick Lichty (pos 1.2, 106 impr, 0 clicks), Clive
Holden (pos 1.5, 75 impr, 0 clicks), Colin Goldberg (pos 1.9, 84 impr, 0 clicks), Frank Gillette
(pos 1.9, 504 impr, 3 clicks). See `Queries.csv` for the full list. `/artists/` has the opposite
problem: 924 impressions but ranks position 29.5 (page 3) — a real visibility gap.

## Phase plan (per Colin, 2026-09-22)

- **Phase 1 — archive-related SEO, before the redirects.** Everything specific to the Video
  Archive itself: `<title>`/description optimization, salon/roundtable lead-name titles, JSON-LD/
  schema, sitemap correctness, internal linking within the archive. Today's title-tag and
  description-length work (this session) counts as Phase 1. Still open under Phase 1: an audit of
  the archive's own internal linking and sitemap ahead of the redirect, so incoming pages land
  well rather than risking the "discovered/crawled but not indexed" limbo described above.
- **Phase 2 — redirect all video pages** into techspressionism.com (previously documented target:
  before 1 October 2026, production via WP Engine). Phase 1 finishes before this starts.
- **Phase 3 — unspecified/TBD** (not yet defined; ask Colin when it's relevant).
- **Phase 4 — general, site-wide SEO optimization**, after Phase 2 has real post-launch data to
  work from: the techspressionism.com-wide findings in this baseline (the 210-page indexing-limbo
  issue, backlink strategy, non-archive pages' titles/descriptions, the duplicate sitemap
  submission) belong here, not before.

Once Phase 2 lands: re-capture this same baseline, diff it against this snapshot, and revisit the
"discovered/crawled but not indexed" 210-page issue with real post-launch numbers.
