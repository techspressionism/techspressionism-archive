#!/usr/bin/env python3
"""List what the archive does not know yet, straight from the data, so the manual's
"Missing or unverified information" section can be kept accurate.

    python3 scripts/missing-info.py

Reads data/sessions.json and the transcript files. Nothing is changed. Each heading says what is
missing and what closes the gap. Add anything new that is not detectable from the data (for example a
fact only Colin knows) to the manual's list by hand.
"""
import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sessions = json.load(open(ROOT / "data" / "sessions.json"))
slug = lambda s: f"{s.get('type', 'salon')}-{int(s['number']):03d}"
by_slug = {slug(s): s for s in sessions}


def show(title, fix, items, note=lambda s: ""):
    print(f"\n{title}  ({len(items)})\n  fix: {fix}")
    for s in items:
        print(f"   - {slug(s)}  {(s.get('session_title') or '')[:46]}  {note(s)}".rstrip())


def has(s, flag):
    return flag in (s.get("flags") or [])


def not_zoom(s):
    return s.get("transcript_source") != "zoom-transcript"


show("Recording date only estimated from the YouTube upload date",
     "Colin supplies the recording date; add it to SITE_DATES in scripts/01c-build-media-sessions.py",
     [s for s in sessions if has(s, "recording_date_estimated_from_upload")], lambda s: f"published {s.get('date_published')}")
show("Recording date incomplete (year or month only)", "find the exact date; add it to SITE_DATES in 01c",
     [s for s in sessions if s.get("date_recorded") and len(s["date_recorded"]) < 10], lambda s: s["date_recorded"])
show("Recording date missing", "same", [s for s in sessions if not s.get("date_recorded")])
show("Date sources disagree (noted in the data)", "decide which is right; correct it on the site if needed",
     [s for s in sessions if any(n.startswith("date_note:") for n in s.get("notes") or [])],
     lambda s: next(n[10:100] for n in s["notes"] if n.startswith("date_note:")))
show("No speaker list with start times, and no Zoom transcript (so nothing names the speakers yet)",
     "add timecodes to the YouTube description / website page, or run voice separation and NameReview",
     # judged from the speakers' actual start times (the flag can be stale when the list came from the website page)
     [s for s in sessions if not_zoom(s) and not any(x.get("start_seconds") is not None for x in s.get("speakers", []))])
show("Speaker list only partly readable", "tidy the description's timecode lines",
     [s for s in sessions if has(s, "speaker_index_partially_unparseable")])
show("Moderator not recorded", "add it in the YouTube description or data/manual-overrides.json",
     [s for s in sessions if has(s, "moderator_missing")])
show("Title could not be read", "fix the title in data/manual-overrides.json", [s for s in sessions if has(s, "session_title_unparseable")])
show("Zoom transcript but no YouTube captions, so its timecodes cannot be checked against the video",
     "run Whisper on the YouTube audio, then check-whisper-alignment.py",
     [s for s in sessions if s.get("transcript_source") == "zoom-transcript" and not glob.glob(str(ROOT / "raw" / "captions" / slug(s) / "*.vtt"))])
show("No transcript at all", "run Whisper", [s for s in sessions if s.get("transcript_source") in (None, "none")])
show("Presenters known but no start times (presentations)", "Whisper + NameReview, or add timecodes to the description",
     [s for s in sessions if s.get("type") == "presentation" and not any(x.get("start_seconds") is not None for x in s.get("speakers", []))])
print("\nNot detectable from the data (check the manual's list): open facts only Colin knows, the cut-speech review,")
print("licence confirmation, credits, WordPress links and the Zenodo deposit.")
