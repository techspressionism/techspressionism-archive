# SEO baseline — 2026-09-22, pre–Phase 2

Captured from Google Search Console (property: `https://techspressionism.com/`) the same day the
Video Archive's `<title>`/description SEO work was done, specifically so we have a documented
"before" state to compare against once the archive is redirected into techspressionism.com
(Phase 2). Do this again after that launch and diff against this snapshot to see what the
redirect actually did to visibility.

**Scope:** this covers the live techspressionism.com site as a whole (its current `/video/...`,
`/salon/`, `/artists/` pages etc.) — not the new archive site under construction, which isn't
indexed yet.

## Headline numbers (trailing 3 months, Search type: Web)

- **284 total clicks, 30.2K total impressions, 0.9% average CTR, 9.9 average position**
  (these are Search Console's own report-level aggregates; the per-page/per-query CSV sums here
  differ slightly — 293 clicks / 35.8K impressions — which is a normal GSC aggregation quirk, not
  an error)
- By device: Desktop 181 clicks / 20,823 impr / 0.87% CTR / pos 11.2; Mobile 99 clicks / 9,112
  impr / 1.09% CTR / pos 7.1; Tablet 4 clicks / 227 impr.

## Files

- `Queries.csv` — 600 rows, every search query with clicks/impressions/CTR/position.
- `Pages.csv` — 287 rows, same breakdown per URL.
- `Countries.csv`, `Devices.csv`, `Search appearance.csv`, `Chart.csv` — supporting breakdowns.
- `Filters.csv` — confirms this export is Web search, trailing 3 months.

## Headline finding (drove the title-tag work this session)

Many individual artist-name queries already rank at position 1–3 (i.e. Google already picks the
right page) but get 0% or near-0% CTR — a snippet/title problem, not a visibility problem. Examples
from this export: Steve Miller (pos 1.1, 73 impr, 0 clicks), Patrick Lichty (pos 1.2, 106 impr, 0
clicks), Clive Holden (pos 1.5, 75 impr, 0 clicks), Colin Goldberg (pos 1.9, 84 impr, 0 clicks),
Frank Gillette (pos 1.9, 504 impr, 3 clicks). See `Queries.csv` for the full list.

Also notable: `/artists/` gets 924 impressions but ranks position 29.5 (page 3) — a real visibility
gap, separate from the CTR issue above.
