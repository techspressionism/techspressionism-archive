# Techspressionist Salon Archive

A searchable, citable transcript archive of the Techspressionism YouTube channel. See [archive_claude_code_spec.txt](archive_claude_code_spec.txt) for the full build spec.

## Status

Build order per spec: prove the pipeline on 3 test sessions (most recent Salon, one from 2021, Salon 48) by hand before batching. Currently mid-way through that pass.

| Stage | Status | Notes |
|---|---|---|
| 1 — Metadata harvest | Done, validated on 3 sessions | `scripts/01-harvest.py`. Also cross-references the WordPress export and YouTube thumbnails (see below) to fill gaps the description alone can't. |
| 2 — Captions/transcript selection | Done, validated on 3 sessions | `scripts/02-transcripts.py`. Source priority differs from the original spec -- see below. |
| 3 — Whisper fallback | Built, **unvalidated** | `scripts/03-whisper-transcribe.py`, via `mlx-whisper` in `.venv` (not system Python -- see below). No real session has needed it yet: cross-referencing local Zoom transcripts against the full YouTube caption batch shows all 99 known Salon videos already resolve via Stage 2. Written ahead of need; per-segment multilingual detection (spec's Salon 48 French note) is a known unimplemented gap -- see the script's docstring. |
| 3b — Push transcript to YouTube as captions | Scaffolded, not runnable | `scripts/03b-upload-captions.py`. Requires OAuth setup Colin hasn't done yet -- see below. |
| 4 — Name correction | Done, validated on 3 sessions | `scripts/04-correct-names.py` + `scripts/04a-parse-artist-index.py`. |
| 5 — Corpus format | In progress | |
| 6 — Search interface | Not started | |
| 7 — Citation/deposit | Not started | |

## Setup

Most scripts only need the system Python 3 (stdlib only, no dependencies). Stage 3 (`03-whisper-transcribe.py`) needs `mlx-whisper`, which isn't installed system-wide (Homebrew's Python blocks that -- PEP 668). Use the project virtualenv instead:

```
python3 -m venv .venv && .venv/bin/pip install mlx-whisper
.venv/bin/python scripts/03-whisper-transcribe.py <salon_number>
```

## Source material, beyond the YouTube channel itself

- **Local Zoom recordings**: `~/Documents/~TECHSPRESSIONISM/VIDEO/SALON/SALON_NNN/`. Zoom's own exports are named specifically -- `*.transcript.vtt/.txt/.pdf` is a real speaker-attributed transcript, `*.cc.vtt` is unattributed closed captions, `*_Recording.txt`/`*newChat.txt` is the in-meeting text chat log (not speech). As of the Aug 2026 inventory: 31 sessions have a real local transcript, 3 have captions-only, 64 have only a chat log, 7 have nothing (of which Salon 02/03 were simply never recorded -- recording started at #8 -- and Salon 110 hasn't happened yet; the real gap is Salon 10, 43, 49, 57).
- **WordPress export** (XML/WXR): far more reliable than scraping techspressionism.com directly, which sits behind Cloudflare bot protection. `scripts/00-parse-wordpress-export.py` parses session descriptions; `scripts/04a-parse-artist-index.py` parses the artist index (post ID 132) into `data/artists.json`.
- **YouTube thumbnails**: recent sessions' custom thumbnail graphic often has the exact recording date printed on it even when the description only gives a month (`raw/thumbnails/{video_id}.jpg`).

## Key decisions made while validating on the 3 test sessions

- **Transcript source priority: Zoom transcript first, YouTube captions as fallback.** Not what the original spec assumed (captions-first). Reasoning: Zoom's transcript is attributed per utterance (turn-by-turn, including Q&A/discussion), where a YouTube caption track is one undifferentiated stream that only Stage 1's manual speaker index can (partially) segment -- and that index only marks where each person's *presentation* starts, not discussion. Confirmed via a same-video duration check (local Zoom recording vs. published YouTube video, 3 sessions spanning 2021-2026) that Colin uploads the raw, untrimmed Zoom recording, so Zoom transcript timestamps map directly onto YouTube timestamps for deep-linking.
- **Transcript text style: "clean verbatim."** Strip non-lexical fillers ("uh", "um") only. Leave everything else exactly as spoken, including "like" and "you know" even when used as discourse filler -- both have legitimate non-filler uses ("I really like this piece") that a naive regex can't safely distinguish from filler use, so the standard clean-verbatim convention sidesteps the problem by not touching those words at all.
- **Controlled vocabulary** (`data/vocabulary.json`): seeded with the spec's list plus observed ASR mishearings of "Techspressionist"/"Techspressionism" (e.g. "text freshness", "tech express system") -- add more as they turn up.
- **Name correction thresholds** (`scripts/04-correct-names.py`): two candidate pools -- a session's own Stage 1 speaker index (strong prior, lower match threshold) and the full ~460-artist index (weak prior, high threshold, multi-word names only for auto-correction). Single-token candidates are never auto-corrected against the weak-prior pool -- too easy to collide with ordinary words (an early pass "corrected" "metal" to "meta"). The artist index itself has a couple of bad entries (two people literally listed with "Techspressionism"/"Abstract Techspressionism" as their name field -- a site data-entry issue) which the correction pass now excludes on principle: any candidate name colliding with a controlled-vocabulary term is dropped from the pool entirely.

## Stage 3b -- push transcripts back to YouTube as captions

For sessions where YouTube never generates auto-captions (it doesn't always -- heavy crosstalk, long recordings, or no visible reason), upload a transcript as a real YouTube caption track via the YouTube Data API's `captions.insert` endpoint, converting from the chosen transcript source (Zoom transcript or Whisper output) to SRT.

**This requires OAuth to Colin's YouTube channel and has NOT been set up.** `scripts/03b-upload-captions.py` is scaffolded with the upload logic but exits immediately if credentials aren't present -- it will not attempt any auth flow itself. To enable:

1. Create a Google Cloud project (or use an existing one) and enable the **YouTube Data API v3**.
2. Create an OAuth 2.0 Client ID (Desktop app type) in that project's credentials page.
3. Download the client secret JSON, save it somewhere outside this repo (never commit it), and set `YT_CLIENT_SECRET_PATH` to its location.
4. Run `scripts/03b-upload-captions.py --auth` once to do the one-time browser authorization; it'll cache a refresh token locally (also gitignored).

This is a deliberate one-time setup step for Colin, not something to do casually or automatically -- do it when ready. Once auth is set up, actual upload runs still need per-session confirmation before publishing, same as any action that modifies public content on Colin's behalf.

## Repository structure

See spec for the intended full layout. `raw/` is gitignored (source audio/video/captions, WordPress export cache). `data/` holds canonical parsed metadata. `review/` holds human-review queues (`name-candidates.csv`, flagged sessions from `sessions.json`'s `flags` field).
