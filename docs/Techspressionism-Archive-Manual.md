# Techspressionism Archive Manual

*How the Techspressionism Video Archive is built, kept up to date, reviewed and published.*

Written 19 September 2026 for the studio M1 (the machine that runs the archive). Where a procedure was **tested** the manual says so. Anything marked **(planned)** is not built yet. Nothing described here is published to the public site until Colin decides.

---

## Contents

1. [What the archive is](#1-what-the-archive-is)
2. [Rules that hold everywhere](#2-rules-that-hold-everywhere)
3. [How it works: the big picture](#3-how-it-works-the-big-picture)
4. [Where everything lives](#4-where-everything-lives)
5. [Setting up a machine](#5-setting-up-a-machine)
6. [Procedures](#6-procedures)
   - 6.1 [Add a new Salon](#61-add-a-new-salon)
   - 6.2 [Add an interview, roundtable or presentation](#62-add-an-interview-roundtable-or-presentation)
   - 6.3 [Get the transcript (Whisper)](#63-get-the-transcript-whisper)
   - 6.4 [Fix the timecodes of recordings edited before upload](#64-fix-the-timecodes-of-recordings-edited-before-upload)
   - 6.5 [Put speaker names on the transcript](#65-put-speaker-names-on-the-transcript)
   - 6.6 [Correct the words](#66-correct-the-words)
   - 6.7 [Public "Suggest a correction"](#67-public-suggest-a-correction)
   - 6.8 [Rebuild](#68-rebuild)
   - 6.9 [Preview the site](#69-preview-the-site)
   - 6.10 [Publish](#610-publish)
   - 6.11 [WordPress and Gravity Forms](#611-wordpress-and-gravity-forms)
7. [Tool reference](#7-tool-reference)
8. [Data reference](#8-data-reference)
9. [Accuracy: what is measured and what is not](#9-accuracy-what-is-measured-and-what-is-not)
10. [Troubleshooting](#10-troubleshooting)
11. [Open items](#11-open-items)
12. [Glossary](#12-glossary)
13. [Appendix: command cheat sheet](#13-appendix-command-cheat-sheet)

---

## 1. What the archive is

A searchable, citable transcript archive of the Techspressionism YouTube channel. Every transcript page links each passage to the exact second of the YouTube video.

| Type | Recordings | Where the source material comes from |
|---|---|---|
| **Salons** | 99 (Salon 2 and 3 were never recorded; recording began at Salon 8) | Zoom recordings kept in `~/Documents/~TECHSPRESSIONISM/VIDEO/SALON/SALON_N/`, uploaded to YouTube, **often edited before upload** |
| **Interviews** | 29 | Mostly Zoom recordings in `VIDEO/INTERVIEWS/<first name>/`; some are edited exports |
| **Roundtables** | 6 | Zoom recordings in `VIDEO/ROUNDTABLE/ROUNDTABLE_N/`. An intro sequence is added, so the YouTube version can be **longer** than the Zoom recording |
| **Presentations** (Hello Uzbekistan) | 8 | **YouTube versions only**, uploaded by Cynthia Beth Rubin. No Zoom originals exist |

About 209 hours in all. The archive builds a static website (search plus one page per recording) that is published through GitHub Pages from the repository `techspressionism/techspressionism-archive`.

---

## 2. Rules that hold everywhere

These are decisions made for the archive. Every tool follows them.

1. **All timecodes belong to the YouTube video.** Every "▶ watch" link and every timestamp plays the YouTube upload, so times must be on *its* timeline. If the YouTube video is shorter than the Zoom recording, sections were cut, and the words are timed against the edited video, not the raw Zoom file. A roundtable's added intro can make the YouTube video longer. Tools that work from Zoom text convert it to the YouTube timeline (section 6.4). **Watch links lead in by 3 seconds:** each "▶ watch" link (on pages and in search results) starts 3 seconds *before* the passage, never before 0:00, so a listener hears the words in context and small timing errors do not cut off the first words. The printed timecode and the citation keep the exact time. The number is `watch_lead_in_seconds` in `data/site-config.json`; the review tools use the same 3 seconds.
2. **Nothing goes public without Colin.** No push to GitHub, no change to the live site, no change to techspressionism.com, until Colin says so.
3. **Clean verbatim.** Only "uh" and "um" are removed. "Like", "you know" and every other word stay as spoken.
4. **Accuracy standard: timestamps and attribution must be right.** A speaker name is shown only when it has been confirmed. Otherwise the passage says **Unattributed**. The archive never guesses and never prints "Speaker 2". No machine transcript is perfectly accurate, so every page tells readers to check a quote with its watch link, and people fix errors through the review tools.
5. **One presentation everywhere.** Every page uses the same layout: a heading per speaker turn with a timestamp and watch link, then paragraphs. No special layouts for particular sources.
6. **Privacy.** The repository is public. Email addresses are never stored in it. A person's name is kept for credit only if they agreed. No voiceprints or face recognition are stored, and voice work happens inside one recording only.
7. **Same input, same output.** Rebuilding must give identical pages every time (a bug that broke this was fixed on 19 September 2026).
8. **Ask before installing anything** on the machine.

---

## 3. How it works: the big picture

```
 YouTube channel + Zoom recordings + techspressionism.com (WordPress export)
                                │
   Stage 1   metadata: titles, dates, speaker lists ──▶  data/sessions.json
   Stage 2   pick the best transcript source, tidy it ─▶  raw/transcripts/
                 (Zoom transcript › Whisper › YouTube captions)
   Stage 2b/2c  put Zoom text on the YouTube timeline; drop cut speech
   Stage 3   Whisper: fresh transcript from the audio ─▶  raw/whisper/
   Stage 3c  Nametag: read the speaker's name from the video
   Stage 3d  separate the voices in the audio
   Stage 4   correct names and known mishearings ─────▶  raw/transcripts_corrected/
   Stage 5   assemble the page: speakers, paragraphs, corrections ─▶ corpus/
   Stage 6   build the website and search index ──────▶  site/
                                │
      Review tools:  NameReview (who is speaking)  ·  TextReview (the words)
```

| Stage | Script | What it does |
|---|---|---|
| 0 | `00-parse-wordpress-export.py` | Reads a WordPress export and caches each Salon page's text (a backup source of speaker lists). |
| 1 | `01-harvest.py` | Turns each video's YouTube metadata into a Salon entry in `data/sessions.json`. **Rebuilds every Salon entry from scratch**, so re-run Stage 2 after it. |
| 1b, 1c | `01b-media-manifest.py`, `01c-build-media-sessions.py` | The same job for interviews, roundtables and presentations. |
| — | `fetch-all-captions.py` | Downloads YouTube's caption files. |
| 2 | `02-transcripts.py` | Chooses each recording's transcript source and cleans it. |
| 2b | `02b-align-zoom-timeline.py` | Works out how far Zoom timestamps must move to match the YouTube video. |
| 2c | `02c-find-cut-material.py` | Finds Zoom speech that was edited out of the video. |
| 3 | `03-whisper-transcribe.py` | Whisper transcription (the accurate one). |
| 3b | `03b-upload-captions.py` | Puts transcripts back on YouTube as captions. **Not runnable yet** (needs YouTube authorization). |
| 3c | `03c-nametag.py` | Nametag: reads the speaker's name from Zoom video. |
| 3d | `03d-diarize.py` | Separates the voices in the audio and names them. |
| 4 | `04-correct-names.py` (+ `04a-parse-artist-index.py`) | Fixes known mishearings and misspelled names. |
| 5 | `05-build-corpus.py` | Builds the transcript pages in `corpus/`. |
| 6 | `06-build-site.py` | Builds the website and the search index. |
| review | `namereview.py`, `textreview.py`, `import-suggestions.py` | The review tools. |
| checks | `check-whisper-alignment.py`, `dedupe-review-queue.py` | Quality checks. |

**Which transcript source each recording uses** (Stage 2 picks the first that exists):

1. The **Zoom transcript**, when one exists. It labels who is speaking on every line.
2. A **Whisper** transcript (`raw/whisper/<slug>.json`).
3. **YouTube captions**: human subtitles if there are any (Cynthia Beth Rubin made the subtitles for most presentations), otherwise automatic captions.

**Where speaker names come from**, in order of trust:

1. **Zoom's own labels** (Zoom-transcript recordings).
2. **Voices you confirmed** in NameReview (Whisper recordings).
3. **Nametag** (only if approved).
4. The **speaker list** with start times in the YouTube description or on the website page (coarse: one name per presenter block).
5. Otherwise **Unattributed**.

---

## 4. Where everything lives

```
techspressionism-archive/
├─ README.md                this repository's overview
├─ docs/                    this manual
├─ scripts/                 every tool (section 7)
├─ data/                    settings, rules and metadata — tracked in git
├─ corpus/                  the finished transcript pages (.md) and corpus.json — tracked
├─ assets/                  thumbnails and images used by the site — tracked
├─ review/                  work queues for people (name candidates)
├─ raw/                     working files — NOT in git (large, or private)
├─ site/                    the built website — NOT in git
├─ .venv/                   Python tools for Whisper (yt-dlp, mlx-whisper, ffmpeg) — NOT in git
└─ .venv-diar/              Python tools for voice separation (pyannote) — NOT in git
```

Files people care about most:

| File or folder | What it is |
|---|---|
| `data/sessions.json` | One entry per recording: type, number, title, dates, video id, speakers, flags, transcript source. Rebuilt by Stages 1, 1c and 2. |
| `data/vocabulary.json` | Words often misheard, with their correct spelling. Applied to every transcript. |
| `data/speaker_aliases.json` | The right spelling of each person's name, and how variants map to it. |
| `data/titles.json` | Book titles that get italicized. |
| `data/artists.json` | The artist index (names, countries). |
| `data/site-config.json` | Site settings: the 3-second watch-link lead-in, the beta "noindex" switch (`beta_noindex`), and the address of the "Suggest a correction" form (empty until set). |
| `data/voice-names/<slug>.json` | Your NameReview decisions. |
| `data/text-edits/<slug>.json` | Your TextReview corrections. |
| `corpus/<slug>.md` | The finished page for one recording. **Generated: do not edit by hand** (a rebuild overwrites it). |
| `raw/whisper/<slug>.json` | The Whisper transcript with word timing. Back this up; Whisper is not exactly repeatable. |
| `raw/cut-material-report.md` | **Private.** Text that was cut from videos. Never commit or share. |

**Names of recordings ("slugs")** are `salon-081`, `interview-005`, `roundtable-002`, `presentation-004`. In most tools a plain number means a Salon.

**Machines and addresses.** The archive runs on the studio M1 (M1 Pro, 32 GB, always on). The review tools run on that Mac only: **NameReview** at `http://localhost:8765` and **TextReview** at `http://localhost:8766`. Always type `localhost`, not `127.0.0.1` or an IP number, or YouTube's embedded player will refuse to play.

**Recordings on disk.** `~/Documents/~TECHSPRESSIONISM/VIDEO/` holds about 445 GB of Zoom recordings. The tools search only the folder belonging to the recording they are working on.

---

## 5. Setting up a machine

Done on the studio M1 in September 2026. Repeat only if setting up a new machine.

| Piece | How it was installed | Used for |
|---|---|---|
| Command Line Tools | Apple's installer | git, the system Python 3.9, the Swift compiler |
| Node.js 24 | Installer from nodejs.org | Builds the search index (`npx pagefind`) |
| `uv` | Downloaded release, checksum verified, placed in `~/.local/bin` | Creates Python environments |
| `.venv` (Python 3.12) | `uv venv --python 3.12 .venv`, then `uv pip install -r requirements.txt` | Whisper, YouTube downloads, ffmpeg |
| `ffmpeg` | Comes inside the `imageio-ffmpeg` package and is linked as `.venv/bin/ffmpeg` | Audio and video handling |
| Whisper model | Downloaded on first use (about 3 GB) into `~/.cache/huggingface` | Transcription |
| `.venv-diar` (Python 3.12) | `uv venv --python 3.12 .venv-diar`, then `uv pip install --python .venv-diar/bin/python pyannote.audio` | Voice separation |
| Hugging Face login | Free account; terms accepted on the model page `pyannote/speaker-diarization-community-1`; `.venv/bin/hf auth login` (browser sign-in) | Lets the voice-separation model download |
| Text reader for Nametag | Compiled automatically on first use with the Swift compiler | Reads names off video frames |

Stages 2, 4, 5 and 6 use only Python's standard library and run on the system `python3`. Whisper, downloads and voice separation need the environments above.

**Recreate `.venv`:**

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
ln -sf "$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" .venv/bin/ffmpeg
```

**Keep the machine awake** for long jobs: keep it on power with the lid open (or use an external display), and run long jobs under `caffeinate -i` as the batch scripts do. Turn off automatic macOS update installs, or an overnight restart will stop a job.

---

## 6. Procedures

### 6.1 Add a new Salon

*Tested on 19 September 2026 with Salon 110, on a scratch copy of the project.* Stage 1 rebuilds every Salon entry from the video files, so it is safe to run again. In the test, nothing changed for the 142 existing recordings except that 55 of them switched to their finished Whisper transcript, which is expected.

1. **Upload the video to YouTube** with a title of the form `Techspressionist Salon 110 // Title`. Put the speaker list with start times in the description (one line each: `HH:MM:SS – Name – Country`). Stage 1 reads it.
2. **Refresh the channel list** (this is how the tools learn a new video exists):

   ```bash
   .venv/bin/yt-dlp --flat-playlist --dump-single-json \
     "https://www.youtube.com/channel/UCIu-35fUEUTDDhRqa5z6zXQ/videos" > raw/channel.json
   ```

3. **Add the Salon to `raw/salon_video_index.json`**, using the number and the video id from the address:

   ```json
   "110": {"video_id": "pMRthFj7aw4", "title": "Techspressionist Salon 110 // Loop Art Critique"}
   ```

4. **Download the video's metadata:**

   ```bash
   .venv/bin/yt-dlp --dump-json --skip-download "https://www.youtube.com/watch?v=pMRthFj7aw4" > raw/video_json/pMRthFj7aw4.json
   ```

5. **Rebuild the session list.** Stage 1 rebuilds every Salon entry from scratch, which drops fields added by later stages (such as the transcript source), so Stage 2 must be run again afterwards (step 8). The interviews, roundtables and presentations are left as they are; run 1b and 1c as well only when the channel list gained a non-Salon video or you changed their tables (section 6.2). Running all three is harmless and is what the test did:

   ```bash
   python3 scripts/01-harvest.py
   python3 scripts/01b-media-manifest.py
   python3 scripts/01c-build-media-sessions.py
   ```

   Check the new entry in `data/sessions.json`: title, `date_recorded`, moderator and speakers with start times. The test read Salon 110 as recorded 2026-07-27 with moderator Tommy Mintz and five timed speakers; **check dates against the video** and fix anything wrong in `data/manual-overrides.json`.
6. **Download the captions:** `python3 scripts/fetch-all-captions.py salon` (recordings already fetched are skipped).
7. **If the Zoom recording is on this Mac**, refresh the file inventory so Stage 2 can find its Zoom transcript:

   ```bash
   python3 scripts/inventory-local-video.py ~/Documents/~TECHSPRESSIONISM/VIDEO/SALON
   ```

8. **Choose the transcript source:** `python3 scripts/02-transcripts.py`. It prints the source it chose for each recording.
9. **Get a Whisper transcript** (section 6.3). Then run Stage 2 again so it is picked up.
10. **If the YouTube video was edited** compared with the Zoom recording, follow section 6.4.
11. **Correct and build the page:**

    ```bash
    python3 scripts/04-correct-names.py salon-110
    python3 scripts/05-build-corpus.py salon-110 --no-review
    python3 scripts/06-build-site.py
    ```

12. **Speaker names** (6.5) and **word corrections** (6.6).
13. Optional: if the description has no speaker list, export the WordPress site and run `00-parse-wordpress-export.py export.xml`. Stage 1 then uses the website page's list.

The test result for Salon 110 (YouTube captions only, before Whisper) was a 65 KB page in 6 speaker sections with 15 vocabulary fixes and 2 name corrections.

### 6.2 Add an interview, roundtable or presentation

Refresh the channel list (step 2 above), then run `python3 scripts/01b-media-manifest.py`. It sorts each new video by its **title**:

| Type | The title must contain | Number comes from | Extra hand-entered details |
|---|---|---|---|
| Interview | "… interviewed by …", with `Interview Series #N` | the `#N` in the title, otherwise the next number | The local Zoom folder (`INTERVIEW_FOLDERS`) and location (`INTERVIEWEE_LOCATIONS`) in `01b`/`01c`, if a Zoom export exists |
| Roundtable | "Roundtable" (or a title listed in `01b`) | `Roundtable #N`, otherwise the next number | Moderator and participants in the `MANUAL` table in `01c-build-media-sessions.py` |
| Presentation | (the Hello Uzbekistan series) | its position in `PRESENTATION_IDS` in `01b` | Add the video id to `PRESENTATION_IDS`; moderator in `MANUAL` in `01c` |

Then:

1. Enter the **recording date** in `SITE_DATES` in `01c` (from the recording's page on techspressionism.com). Without it the page uses the YouTube upload date and says "published" instead of "recorded". Use `DATE_NOTES` to explain any oddity.
2. Run `python3 scripts/01c-build-media-sessions.py`, then `fetch-all-captions.py`, `02-transcripts.py`, and the rest as for a Salon (Whisper 6.3, then 04, 05, 06).

Notes by type:

- **Roundtables:** an intro sequence is added before upload, so the YouTube video may be longer than the Zoom recording and the Zoom timecodes will be offset. Run section 6.4.
- **Presentations:** there are no Zoom recordings, only the YouTube versions (uploaded by Cynthia Beth Rubin). There is no Zoom timeline to reconcile. Transcripts come from her human subtitles where they exist, otherwise automatic captions, or Whisper (the audio is downloaded from YouTube).
- **Interviews:** many local files are edited exports. If a file has no name label on screen, Nametag will report that and skip it.

### 6.3 Get the transcript (Whisper)

Whisper produces the most accurate transcript, with real punctuation. It is used for every recording that has no Zoom transcript.

**One or a few recordings:**

```bash
.venv/bin/python scripts/03-whisper-transcribe.py salon-110 interview-004
```

Options: `--force` redoes a finished recording; `--keep-audio` keeps the audio file (about 115 MB per hour; by default it is deleted).

**How it chooses the audio:**

1. The original recording in that recording's own folder, if a file's length is within 1% of the YouTube video's (`.m4a` preferred). This keeps Whisper's times on the YouTube timeline.
2. Otherwise it downloads the audio from YouTube.

**Safety check:** the finished transcript is compared with YouTube's own captions. If they do not agree, the result goes to `raw/whisper/rejected/` and is not used, because it is probably the wrong recording.

**Speed:** about 4 times faster than real time on this M1 (a 90-minute Salon takes about 22 minutes). Do not run two Whisper jobs at once; each slows the other.

**A big batch** (how the September 2026 batch of 96 recordings ran): put the recording names in a text file, one per line, and start a background script that survives closing the session:

```bash
cat > raw/my_batch.sh <<'EOF'
#!/bin/zsh
cd /Users/colin/techspressionism-archive
nice -n 10 .venv/bin/python -u scripts/03-whisper-transcribe.py $(cat raw/my_batch_slugs.txt)
EOF
chmod +x raw/my_batch.sh
nohup caffeinate -i raw/my_batch.sh > raw/my_batch.log 2>&1 &
```

The script skips recordings that are already done, so if it is interrupted, run it again and it resumes. A failed recording prints the reason. `python3 raw/whisper_status.py` shows progress and a finish estimate for the September batch (it reads `raw/whisper_batch.log`; copy and adjust it for a new batch).

**After Whisper:** run `02-transcripts.py` (it now prefers the Whisper transcript), then 04, 05, 06. Check the timing with `python3 scripts/check-whisper-alignment.py` (each recording should say ALIGNED).

**Two cautions.** Whisper is not exactly repeatable: two runs on the same audio differ slightly, so keep `raw/whisper/`. And it can mishear names and unusual terms, so those still need the correction steps in 6.6.

### 6.4 Fix the timecodes of recordings edited before upload

A Zoom transcript is timed against the *raw* Zoom recording. If the video was edited before upload (sections cut, a start trimmed, an intro added), every Zoom time is wrong for the YouTube video. This applies only to recordings that have a Zoom transcript; Whisper transcripts are already on the YouTube timeline.

**How to tell:** a YouTube video shorter than the Zoom recording means it was cut. Lengths that match to the second mean it was not. A trimmed start or an added intro can also shift times without a length difference, so the tools test the actual text rather than the length.

```bash
python3 scripts/02b-align-zoom-timeline.py salon-094      # or no name = every Zoom recording
python3 scripts/02c-find-cut-material.py salon-094
python3 scripts/02-transcripts.py salon-094
python3 scripts/04-correct-names.py salon-094
python3 scripts/05-build-corpus.py salon-094 --no-review
```

- **2b** matches phrases between the Zoom text and YouTube's captions and writes the time shifts to `raw/timeline/<slug>.json`. It typically lands within about one second.
- **2c** finds the Zoom speech that was cut from the video. It writes `raw/cut-material-report.md` (**private: it holds text you chose to remove**). Only cuts it is confident about are left out of the page; the rest are marked "review" in that file.
- Stage 2 applies both automatically.
- Both need the recording's YouTube captions (`raw/captions/<slug>/`). Without them they cannot check, and the recording should be checked by running Whisper on its YouTube audio.

Recordings handled this way in September 2026: Salons 90, 93, 94, 95, 97, 98, 100, 103, 106, 107, Interview 28, Roundtables 5 and 6. Still to be checked: Salons 87, 101 and 109.

### 6.5 Put speaker names on the transcript

**Recordings with a Zoom transcript** (30 Salons plus a few interviews and roundtables): Zoom labels every line. Nothing to do, except check the few that still show "Unattributed" blocks (Salons 77, 78, 79, 80, 87, 100).

**Recordings without one** (the Whisper recordings): Whisper says what was said but not who said it. Three sources of names are combined, and a voice gets a name only when it is confirmed.

#### Step 1: Nametag (optional)

Reads the speaker's name from Zoom's speaker view or its screen-share thumbnail, every 2 seconds.

```bash
.venv/bin/python scripts/03c-nametag.py salon-081
```

It runs about 12 times faster than real time. It only works on a speaker-view video: a gallery view does not mark who is talking, and edited exports usually have no label (the tool reports "NOT USABLE"). A pinned or spotlighted camera fools it: the name stays on screen while other people talk. So Nametag is never used on its own.

#### Step 2: Separate the voices

```bash
.venv-diar/bin/python scripts/03d-diarize.py salon-081            # separate voices and suggest names
.venv-diar/bin/python scripts/03d-diarize.py --score salon-081    # also compare with Zoom's labels (Zoom recordings only)
.venv-diar/bin/python scripts/03d-diarize.py --rename salon-081   # redo the naming only (no model needed)
```

About 8 minutes for a 90-minute Salon. It writes `raw/diarize/<slug>.json`: the voices (V1, V2, …) and who is speaking when, and each voice's suggested name and tier:

| Tier | Meaning | Measured reliability |
|---|---|---|
| **confirmed** | The on-screen name **and** the speaker list both name the voice and agree | 18 of 18 right in the test |
| **single** | Only one source names it | about 87–92% right; needs review |
| **conflict** | The two sources disagree | needs review |
| **none** | Nothing names it | stays Unattributed |

Run it while no Whisper batch is running.

#### Step 3: NameReview (a person confirms who is who)

```bash
python3 scripts/namereview.py        # then open http://localhost:8765
```

1. Choose a recording. Each **voice** is a card, largest first, showing how much it speaks, the guessed name and where it came from, and three short audio clips with the words spoken.
2. Play the clips. Press **▶ Show in video** to watch that moment in the video at the top of the page.
3. If the name is right, press **Confirm**. If not, type the right name in the box (suggestions appear as you type) and press **Save**. Press **Leave unattributed** if you cannot tell; that is always safe.
4. Tick **"Remember that the on-screen name X means this person"** to add that display name to `data/speaker_aliases.json` for all future recordings.
5. The tick box at the top, **"Approve the automatically confirmed names,"** accepts every voice the two sources agreed on.
6. Your choices are saved at once to `data/voice-names/<slug>.json`. A choice is marked STALE if voice separation is re-run and the voice is no longer the same.
7. **Publish the result:** NameReview does not rebuild pages. Run `python3 scripts/05-build-corpus.py salon-081 --no-review` (then `06-build-site.py`).

Names from NameReview are used **only for Whisper-based recordings**. Zoom recordings keep Zoom's labels, so reviewing them there is practice. Nothing in `raw/nametag/` or `raw/diarize/` is used until you have confirmed or approved it.

### 6.6 Correct the words

There are three kinds of fix. Use the first for anything that recurs.

**1. A rule that applies to every transcript** (best for a repeated mishearing):
- Add the wrong and right spelling to **`data/vocabulary.json`**.
- Add a person's correct spelling or an alias to **`data/speaker_aliases.json`**.
- Add a book title to **`data/titles.json`** so it is italicized.
- Then rebuild (6.8).

**2. The name-review queue.** Stage 4 writes possible name fixes to `review/name-candidates.csv`. Run `python3 scripts/dedupe-review-queue.py` to collapse it to `review/name-candidates-summary.csv` (unique patterns with a count; high counts are usually noise). Pairs you decide are always false go in `data/review-suppressions.json`. Clear the raw CSV before a full rebuild, or it accumulates duplicates.

**3. One-off fixes: TextReview.**

```bash
python3 scripts/textreview.py        # then open http://localhost:8766
```

1. Choose a recording.
2. Enter **your name** at the top (it is kept with each correction so volunteers can be credited).
3. Each paragraph has a box. Press **▶** to watch that moment in the video at the top of the page, correct the words in the box, and press **Save**.
4. **Show original** displays the machine text; **Undo my correction** removes yours; the **Find** box searches the transcript.
5. Press **Apply corrections to the page** to rebuild that recording's page so the correction reaches the corpus, the site and search.

How it works underneath: corrections are stored in `data/text-edits/<slug>.json`, **separate from the machine text**. Each records the exact paragraph it changes. At every rebuild, Stage 5 applies them last. If the machine text has since changed so that the paragraph no longer exists (for example after re-running Whisper), the correction is listed as **STALE**; it is never applied to the wrong place, and you re-make it on the new text.

Do **not** edit files in `corpus/` by hand: a rebuild overwrites them.

### 6.7 Public "Suggest a correction"

Lets visitors and trusted volunteers propose fixes without touching the archive. You approve every one.

1. Each paragraph on the site can carry a **Suggest a correction** link that opens a Gravity Form on techspressionism.com with the recording, time and passage already filled in. **The link is hidden until you set the form's address** in `data/site-config.json` (`suggest_url`), so nothing changes on the site until then. After setting it, rebuild the site (`06-build-site.py`).
2. In WordPress, export the form's entries (Forms → Entries → Export) as a CSV.
3. Import them:

   ```bash
   python3 scripts/import-suggestions.py entries.csv --dry-run    # look first
   python3 scripts/import-suggestions.py entries.csv
   ```

   Each suggestion is matched to its paragraph and filed as **pending** in `raw/suggestions/<slug>.json`. That folder is **not** in git, because it can hold names and notes. Importing the same file twice is safe. The tool never reads the email column, removes email addresses from notes, and keeps a name only if the person ticked the consent box.
4. In TextReview, open **Suggestions** (a banner on the home screen). Each shows the change highlighted (red removed, green added), who sent it (or "anonymous"), the note, and a ▶ button to watch the moment. Choose **Approve as written**, **Edit first…** then approve, or **Reject**. Approved ones become corrections in `data/text-edits/`.
5. Press **Rebuild pages with the approvals**, or run `05-build-corpus.py` for those recordings.

Long passages: only the first 1,200 characters travel in the link. The importer keeps the untouched ending of the paragraph when it applies the correction.

### 6.8 Rebuild

Everything is generated from the source files, so rebuilding is safe. Give recording names to limit the work; with none, every recording is processed.

**One recording:**

```bash
python3 scripts/02-transcripts.py salon-081
python3 scripts/04-correct-names.py salon-081
python3 scripts/05-build-corpus.py salon-081 --no-review
```

**Everything**, for example after changing `vocabulary.json` (about 20–30 minutes):

```bash
python3 scripts/02-transcripts.py
rm -f review/name-candidates.csv
python3 scripts/04-correct-names.py
python3 scripts/05-build-corpus.py
python3 scripts/dedupe-review-queue.py
python3 scripts/06-build-site.py
```

- `--no-review` (Stage 5) skips adding to the name-review queue; use it for small rebuilds.
- **Order matters.** Stage 1 rebuilds every Salon entry and drops fields added later, so after it always re-run Stage 2 (and 1b, 1c if non-Salon recordings changed).
- Passing a list from a file: `python3 scripts/05-build-corpus.py $(cat list.txt)`. In the Mac's default shell, a variable such as `$LIST` is **not** split into separate words; `$(cat file)` is.
- Corrections (6.6) and confirmed names (6.5) survive rebuilds.

### 6.9 Preview the site

```bash
python3 scripts/06-build-site.py            # builds site/ and the search index (needs Node)
cd site && python3 -m http.server 8000      # then open http://localhost:8000
```

`--no-index` skips the search index for a quick build. Search works only after indexing. The site uses `localhost` here; nothing is published.

### 6.10 Publish

**Only Colin decides what goes live, with one standing exception: this manual.** Colin's rule (19 September 2026): *whenever the manual is updated locally it is pushed*, because the manual is the most important thing to keep current online. That push contains only files under `docs/` (and the README pointer); it never carries other work. It is checked first for tokens, email addresses and private material. A docs-only push does not redeploy the site (the workflow ignores changes under `docs/`).

**While the archive is in beta,** `data/site-config.json` has `"beta_noindex": true`, so every page asks search engines not to list it. The site still works for anyone with the address. Set it to `false` at launch.

How publishing works:

- The repository is `techspressionism/techspressionism-archive` (public). A workflow (`.github/workflows/deploy.yml`) rebuilds the site from the committed files (`corpus/corpus.json`, `assets/`, `data/site-config.json`) and publishes it with GitHub Pages **every time something is pushed to `main`**.
- **Warning:** the pages currently live on the site are from before September 2026. For the Zoom Salons listed in 6.4 they show speech that was cut from the video and offset watch links. Publishing the current version corrects that. Old versions remain visible in the repository's history.
- Files that must never be committed: anything in `raw/` (already ignored, including `cut-material-report.md`), `.venv*`, exports containing emails.

**Before the first public release** (the "launch gate" agreed with Colin), all of these should be true:

- [ ] Transcripts polished; Whisper batches finished and checked (`check-whisper-alignment.py`).
- [ ] Timecodes on the YouTube timeline for every recording (6.4), including Salons 87, 101, 109.
- [ ] Speaker attribution reviewed to the standard in section 2.
- [ ] The date for Interview 15 (June 1, 2021) corrected on techspressionism.com.
- [ ] Credit line for Cynthia Beth Rubin's subtitles added.
- [ ] Licence confirmed with Colin (intended CC BY 4.0) and a `LICENSE` file added.
- [ ] WordPress links tested on staging (6.11); "Suggest a correction" form tested on the dev or staging site.
- [ ] Zenodo deposit (README, "Depositing to Zenodo").

### 6.11 WordPress and Gravity Forms

techspressionism.com is on WP Engine, which has **dev and staging** environments and uses **Gravity Forms**. Always test on dev or staging; Colin pushes to production himself. Never give an administrator password to the tools.

**Build the "Suggest a correction" form** (on dev):

| Field | Type | Pre-fill parameter |
|---|---|---|
| Recording | hidden | `recording` |
| Time | hidden | `time` |
| Passage (as it appears) | paragraph text | `passage` |
| Corrected passage | paragraph text, pre-filled with the same text | `passage` |
| Note | paragraph text, optional | — |
| Name | name, optional | — |
| Credit me by name | checkbox, optional | — |
| Email | email, optional, never published | — |

Turn on "Allow field to be populated dynamically" for the first four fields, using the parameter names above (they are configurable under `suggest_params` in `data/site-config.json`). Put the form on its own page, add spam protection, and set that page's address as `suggest_url`.

**Links from each video's page to its transcript (planned).** Needs a staging address, an Editor account with an application password created by Colin, and the link placement; it would begin as a dry-run report.

---

## 7. Tool reference

All commands are run from the project folder. `python3` is the system Python; `.venv/bin/python` has Whisper; `.venv-diar/bin/python` has voice separation.

### Building the archive

| Tool | Use | Reads → writes |
|---|---|---|
| `00-parse-wordpress-export.py export.xml` | Cache the text of each Salon's website page | WordPress export → `raw/site_text/` |
| `01-harvest.py [files]` | Salon metadata; rebuilds the Salon list | `raw/video_json/*.json` → `data/sessions.json` |
| `01b-media-manifest.py [--no-fetch]` | Classify interviews, roundtables, presentations | `raw/channel.json` → `data/media-manifest.json` |
| `01c-build-media-sessions.py` | Add those to the session list | manifest → `data/sessions.json` |
| `fetch-all-captions.py [types]` | Download YouTube captions | YouTube → `raw/captions/` |
| `inventory-local-video.py <folder>` | List the recordings on this Mac | disk → `data/local-video-inventory.*` |
| `02-transcripts.py [names]` | Choose and clean the transcript source; applies timeline shifts and cuts | captions, Zoom, Whisper → `raw/transcripts/`, `sessions.json` |
| `02b-align-zoom-timeline.py [names]` | Time shifts for edited recordings | Zoom + captions → `raw/timeline/<slug>.json` |
| `02c-find-cut-material.py [names]` | Find speech cut from the video | → `raw/timeline/<slug>.cut.json`, `raw/cut-material-report.md` |
| `03-whisper-transcribe.py [--force] [--keep-audio] names` | Whisper | audio → `raw/whisper/<slug>.json` |
| `03c-nametag.py [--force] [--max-minutes N] names` | Read on-screen speaker names | video → `raw/nametag/<slug>.json` |
| `03d-diarize.py [--rename] [--score] names` | Separate and name voices | audio → `raw/diarize/<slug>.json` |
| `04-correct-names.py [names]` | Fix mishearings and names | → `raw/transcripts_corrected/`, `review/name-candidates.csv` |
| `04a-parse-artist-index.py export.xml` | Rebuild the artist index | WordPress export → `data/artists.json` |
| `05-build-corpus.py [--no-review] [names]` | Build the pages | → `corpus/<slug>.md`, `corpus/corpus.json` |
| `06-build-site.py [--no-index]` | Build the website and search | `corpus/` → `site/` |
| `03b-upload-captions.py` | Put transcripts on YouTube as captions | **Not runnable**: needs YouTube authorization (README, "Stage 3b") |
| `batch-all.py` | The original all-Salons driver from the first build; use the procedures above instead | — |

### Review and checking

| Tool | Use |
|---|---|
| `namereview.py [--port] [--host]` | NameReview (6.5). Port 8765. |
| `textreview.py [--port] [--host]` | TextReview and the Suggestions queue (6.6, 6.7). Port 8766. |
| `import-suggestions.py file.csv [--dry-run]` | Import Gravity Forms entries as pending suggestions (6.7). |
| `check-whisper-alignment.py [names]` | Confirms Whisper timestamps match YouTube's captions. Exits with an error if any recording is not ALIGNED. |
| `dedupe-review-queue.py` | Collapse the name-review queue. |
| `missing-info.py` | Lists what the archive does not know yet (dates, speaker lists, moderators, unverified timecodes). Feeds the manual's "Missing or unverified information" list. |
| `raw/whisper_status.py [--watch]` | Progress and finish estimate for the September 2026 Whisper batch. |

The review tools serve only this Mac. There is no login: never point `--host` at an address other people can reach.

---

## 8. Data reference

**`data/text-edits/<slug>.json`** (TextReview corrections; committed)

```json
{"edits": [{
  "id": "e04642950f", "t": 478.2,
  "old": "the machine paragraph, exactly", "new": "the corrected paragraph",
  "by": "Name or 'Public suggestion'", "at": "2026-09-19 13:26",
  "note": "", "status": "approved"
}]}
```

Only `approved` edits are applied. `old` must match a paragraph exactly, otherwise the edit is stale.

**`data/voice-names/<slug>.json`** (NameReview decisions; committed)

```json
{"approved_auto": false,
 "voices": {"V2": {"name": "Tommy Mintz", "fingerprint": "7d390fdd3f", "at": "2026-09-19 13:11"}}}
```

An empty `name` means "leave unattributed". `fingerprint` ties the decision to that voice; a mismatch marks it stale.

**`raw/diarize/<slug>.json`**: voice turns (`[start, end, "V1"]`) and each voice's `screen`, `index`, `tier`, `name`, `candidate`. **`raw/nametag/<slug>.json`**: name readings every 2 seconds. **`raw/timeline/<slug>.json`**: time shifts, `[[zoom_seconds, shift], …]`. **`raw/suggestions/<slug>.json`**: pending public suggestions (private).

**`data/sessions.json`**: fields include `type`, `number`, `session_title`, `date_recorded`, `date_published`, `video_id`, `url`, `duration_seconds`, `moderator`, `speakers` (name, country, `start_seconds`), `flags`, `transcript_source`.

**Page format (`corpus/<slug>.md`)**: a header block (title, dates, video, speakers, transcript source, flags) and then, for each turn, `## Speaker [mm:ss](link)` followed by paragraphs. `corpus/corpus.json` holds the same content for the website and search.

**Flags** on a recording: `speaker_index_missing`, `moderator_missing`, `transcript_quality_low`, `no_transcript_source_available`. A page with per-line Zoom labels does not show "speaker index missing".

---

## 9. Accuracy: what is measured and what is not

| Question | Result (September 2026) |
|---|---|
| Do Whisper timestamps match the YouTube video? | Yes: 75–93% word-pair agreement with YouTube's captions in the same time windows, against 1–4% when shifted. Checked with `check-whisper-alignment.py`. |
| Are Zoom timestamps right? | 19 of 35 needed no shift. 13 needed shifting and were corrected (about 1 second typical error). 3 could not be tested (no YouTube captions). |
| Is Whisper better than YouTube's captions? | On the recordings compared, yes: fewer wrong words, real sentences, 5–20% more words captured. No formal error rate has been measured. |
| Nametag | Reliable on about 3 of 10 test Salons, partial on 4, failing on 3. Never used alone. |
| Voice separation | Voices told apart correctly 96–98% of the time on most tests (82% on one heavily edited Salon). Naming is the weaker step. |
| Confirmed voice names | 18 of 18 right (both sources agreed). One source alone: 87–92% right. |
| Text accuracy | Not measured. A good measure would be to count errors in about 20 random 2-minute passages against the audio. |

Machine transcripts are never perfect. The safeguards are: timestamps checked by tools, names shown only when confirmed, corrections stored separately and reviewable, and a visible warning on the site.

---

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| YouTube player says "video unavailable" or Error 153 in a review tool | The page was opened by IP number. Use `http://localhost:8765` or `:8766`. |
| A list of names in the terminal does nothing | In the default Mac shell, `$LIST` is not split into words. Use `$(cat file)`. |
| A Whisper download failed ("yt-dlp failed…") | Usually temporary. Run the batch again; finished recordings are skipped. |
| A Whisper batch seems slow | Check nothing else is transcribing. Two jobs at once halve the speed. |
| The Mac went to sleep mid-job | Keep it on power, lid open, and run under `caffeinate -i`. |
| `check-whisper-alignment.py` says NOT ALIGNED | The audio used does not match the YouTube video. Do not use that transcript; re-run Whisper with the right audio. |
| A TextReview correction shows STALE | The machine text changed. Re-make the correction on the current text. |
| A NameReview decision shows STALE | Voice separation was re-run and the voices changed. Review that recording again. |
| Pages differ between two identical builds | Should not happen (fixed 19 Sept 2026). If it recurs, run the same build with two different `PYTHONHASHSEED` values and compare. |
| `review/name-candidates.csv` keeps growing | Stages 4 and 5 append. Delete it before a full rebuild, and use `--no-review` for small ones. |
| After Stage 1, the transcript sources are missing | Stage 1 rebuilds every Salon entry and drops later fields. Re-run Stage 2 (and 1b, 1c if non-Salon recordings changed). |
| "Port already in use" starting a review tool | `pkill -f scripts/namereview.py` (or `textreview.py`). |
| Hugging Face refuses to load the model | Accept the model's terms on huggingface.co, then `.venv/bin/hf auth login` (browser sign-in). Check with `.venv/bin/hf auth whoami`. |
| Cannot fetch techspressionism.com with `curl` | Cloudflare blocks it. Use a WordPress export instead. |

---

## 11. Open items

- **Speaker attribution for the Whisper recordings:** run voice separation on all of them (about 12 hours), then review the voices in NameReview. Connecting confirmed names into pages is built and tested.
- **Zoom-labelled text with Whisper's words:** for the 13 recordings edited before upload, run Whisper and carry Zoom's speaker names over (not built). Salon 57 is queued to run after the main batch.
- **Salons 87, 101, 109:** confirm their timecodes.
- **Cut speech:** review the cuts marked "review" in `raw/cut-material-report.md`.
- **Spelling check:** a flag-only check with the archive's own name lists (never auto-correct).
- **Text accuracy audit:** measure a real error rate.
- **Volunteer editing online:** through WordPress (trusted users, or the Gravity Form) rather than any access to this Mac.
- **A one-command "add a recording" tool** (today it is the step list in 6.1 and 6.2).
- **Credit** for volunteers' corrections and Cynthia Beth Rubin's subtitles on the pages.
- **WordPress links** from each video's page to its transcript.
- **Zenodo deposit** and licence file.
- **Live site:** publish the corrected pages (6.10).

### Missing or unverified information

The archive knows what it does not know. **Every gap belongs on this list.** To see the current list from the data, run `python3 scripts/missing-info.py`; each heading says what closes the gap. Facts only a person can supply are added here by hand. *Snapshot: 19 September 2026.*

| What is missing | Recordings | What closes it |
|---|---|---|
| **Recording date** known only as the YouTube upload date | Presentations 3, 4, 5, 6, 7, 8 (only 1 and 2 state a date on techspressionism.com: February 12 and 26, 2026) | Colin supplies the dates; enter them in `SITE_DATES` in `01c-build-media-sessions.py`. Clues so far: Roz Dimon calls Presentation 6 "the third presentation" of the series and Michael Pierre Price calls Presentation 8 "the final presentation", although the site lists them 6th and 8th |
| **Recording date incomplete** | Interview 4 (year 2000 only), Interview 26 (March 2023, no day) | Find the exact date |
| **Dates that disagree** | Interview 13 (the site gives the same date as Interview 12: possible copy and paste), Interview 22 (YouTube says March 1, the site says March 2), Interview 15 (the archive says June 1, 2021 as Roz Dimon states on the recording; **techspressionism.com still says June 2 and must be changed there**), Roundtable 2 (the site page says 2022; the recording is January 23, 2023, confirmed) | Correct the site pages |
| **Salon 110** is on the YouTube channel but not yet in the archive; its recording date was read as 2026-07-27 | Salon 110 | Add it (6.1) and verify the date |
| **Who is speaking: no speaker list with start times and no Zoom transcript** | 17 Salons (26, 29, 40, 41, 43 to 55), 26 interviews (1 to 26), Roundtables 1 to 4, Presentations 6 and 8 | Voice separation and NameReview (6.5), or add timecodes to the YouTube description or website page |
| **Presenters known but no start times** | Presentation 6 (Lee Day, Gregory Little, Lee Musgrave; moderator Roz Dimon), Presentation 8 (Lucy Boyd-Wilson, Annette Weintraub, Ramis Karimov, Jafar Rustamov; moderator Michael Pierre Price). Names were taken from the thumbnail graphics and confirmed from the moderators' spoken introductions | Whisper plus NameReview, or timecodes added to the description |
| **Speaker list only partly readable** | Salons 35, 37, 57, 59, 63, 77, 94, 100 | Tidy the timecode lines in the description |
| **Moderator not recorded** | 22 Salons (15, 26, 29, 33, 35, 37, 38, 44, 45, 48, 56, 60, 62, 67, 87, 93, 94, 95, 97, 100, 106, 108) | Add to the description or `data/manual-overrides.json` |
| **Title could not be read** | Salon 98 | Fix in `data/manual-overrides.json` |
| **Timecodes not checked against the video** (a Zoom transcript but no YouTube captions to compare) | Salons 87, 101, 109 (87 and 101 are the same length as their Zoom recordings, so they are probably unedited; 109 has no Zoom recording on this Mac to compare) | Run Whisper on their YouTube audio, then `check-whisper-alignment.py` |
| **Speech cut from the video, not yet reviewed** | Salons 90, 93, 94, 97, 98, 100, 103, 106, 107, Roundtable 5 (the uncertain cuts) | Review `raw/cut-material-report.md` (private) |
| **Embedded video on the presentations page** | techspressionism.com/presentations | The page's code refers to Presentation 7's video (`lW17XAepKs4`) in the Presentation 8 section. Check that the page shows the right video for each presentation |
| **Interview 15: email address** as heard by Whisper ("darcygehrbarg .Com") | Interview 15 | Check the spelling against the video (about the 32-minute mark) |
| **Speaker attribution for the Whisper recordings** | 96 recordings | Run voice separation, then review in NameReview |
| **Whisper transcripts still being made** | See section 6.3; the batches finish about Sunday 20 September | Wait, then run the alignment check |
| **Credits and permissions** | Cynthia Beth Rubin's subtitles need a credit line; the licence (intended CC BY 4.0) needs Colin's confirmation | Decide and add |

Presentations note: there are exactly 8 published presentations on techspressionism.com, in the same order as the archive. They exist only as YouTube uploads by Cynthia Beth Rubin.

---

## 12. Glossary

- **Slug**: a recording's short name, such as `salon-081`.
- **Stage**: one step in the build (section 3).
- **Corpus**: the finished transcript pages in `corpus/`.
- **Whisper**: the speech-to-text program that transcribes the audio.
- **Zoom transcript**: the transcript Zoom itself produced, labelled by speaker.
- **Timeline**: the clock a set of timestamps refers to. The archive uses the YouTube video's.
- **Diarization / voice separation**: working out which stretches of audio belong to the same voice, without knowing names.
- **Nametag**: reading the speaker's on-screen name from the video.
- **Confirmed / single / conflict / none**: how well supported a voice's name is (section 6.5).
- **Unattributed**: text where the speaker is not known.
- **Stale**: a saved decision or correction that no longer matches the current transcript, so it is not applied.
- **Speaker index**: the list of names with start times in a YouTube description or website page.
- **Staging / dev**: WP Engine's test copies of the website.

---

## 13. Appendix: command cheat sheet

```bash
# add a Salon (6.1)
.venv/bin/yt-dlp --flat-playlist --dump-single-json "https://www.youtube.com/channel/UCIu-35fUEUTDDhRqa5z6zXQ/videos" > raw/channel.json
#   edit raw/salon_video_index.json, then:
.venv/bin/yt-dlp --dump-json --skip-download "https://www.youtube.com/watch?v=VIDEOID" > raw/video_json/VIDEOID.json
python3 scripts/01-harvest.py && python3 scripts/01b-media-manifest.py && python3 scripts/01c-build-media-sessions.py
python3 scripts/fetch-all-captions.py salon && python3 scripts/02-transcripts.py

# transcript
.venv/bin/python scripts/03-whisper-transcribe.py salon-110
python3 scripts/check-whisper-alignment.py salon-110

# edited before upload
python3 scripts/02b-align-zoom-timeline.py salon-094 && python3 scripts/02c-find-cut-material.py salon-094

# speakers
.venv/bin/python scripts/03c-nametag.py salon-110
.venv-diar/bin/python scripts/03d-diarize.py salon-110
python3 scripts/namereview.py            # http://localhost:8765

# words
python3 scripts/textreview.py            # http://localhost:8766
python3 scripts/import-suggestions.py entries.csv --dry-run

# rebuild one recording, then the site
python3 scripts/02-transcripts.py salon-110 && python3 scripts/04-correct-names.py salon-110 && python3 scripts/05-build-corpus.py salon-110 --no-review
python3 scripts/06-build-site.py
```
