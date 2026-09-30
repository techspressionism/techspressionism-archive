#!/usr/bin/env python3
"""Apply a volunteer editor's corrections from a downloaded-and-edited TXT transcript back into the archive.

    python3 scripts/import-edited-transcript.py salon-020 ~/Downloads/salon-020-edited.txt --by "CBR"
    python3 scripts/import-edited-transcript.py salon-020 ~/Downloads/salon-020-edited.txt --by "CBR" --dry-run

The editor downloads a recording's TXT transcript from its "Download transcript as" menu on the archive
(scripts/06-build-site.py: write_txt / download_select_html), which carries a [H:MM:SS] timecode before
every speaker's name -- reference only, not shown on the site. She edits wording/punctuation freely and
can fix a wrong speaker name, then sends the file back; this script reads it against the current corpus:

  - Text changes are filed as PENDING suggestions in raw/suggestions/<slug>.json -- the exact same place
    and review workflow as the public "Suggest a correction" form. Nothing goes live until reviewed and
    approved in TextReview (python3 scripts/textreview.py -> Suggestions).
  - Speaker (attribution) changes are written straight to data/speaker-fixes/<slug>.json with
    "override": true (so an already-attributed turn can be corrected, not just a blank one filled in --
    see 05-build-corpus.py). There is no separate review queue for these, so every one found is printed
    here for you to look over before running 05-build-corpus.py to actually rebuild the recording.

Each turn in the edited file is matched to the original segment IN ORDER (position); its timecode is only
a sanity check (it should be exactly as exported, since editors are asked not to change it). If the file's
turn count doesn't match the recording's segment count, or a timecode has drifted by more than 5 seconds,
the whole run stops without writing anything -- positional matching can no longer be trusted past that
point, and guessing a realignment risks silently misattributing text. Only the TRANSCRIPT section is read;
Synopsis and Participants are for the editor's context only and are never re-imported (their timecode-
linked markup can't be reconstructed from plain edited text -- see EDITOR_INSTRUCTIONS in 06-build-site.py).
"""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from lib_media import label, slug  # noqa: E402

CORPUS_JSON = ROOT / "corpus" / "corpus.json"
SUGGESTIONS_DIR = ROOT / "raw" / "suggestions"
SPEAKER_FIXES_DIR = ROOT / "data" / "speaker-fixes"
TURN_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s+(.+?)\s*$", re.M)
PLACEHOLDER_SPEAKERS = {"Unattributed", "Transcript"}   # what transcript_report_blocks() shows for a blank speaker
DRIFT_TOLERANCE = 5.0   # seconds a turn's timecode may have drifted before matching is no longer trusted


def parse_edited_file(path):
    """[(seconds, speaker, text), ...] for every turn in the TRANSCRIPT section."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^## Transcript\s*$", text, re.M)
    if not m:
        sys.exit('Could not find a "## Transcript" heading in this file -- is it the TXT download, unedited above that line?')
    body = text[m.end():]
    matches = list(TURN_RE.finditer(body))
    if not matches:
        sys.exit("No [H:MM:SS] Speaker Name turns found under Transcript -- were the timecode lines removed?")
    turns = []
    for i, tm in enumerate(matches):
        h, mi, s, speaker = tm.groups()
        seconds = int(h) * 3600 + int(mi) * 60 + int(s)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        turns.append((seconds, speaker.strip(), body[tm.end():end].strip()))
    return turns


def split_paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def load_suggestions(slug_):
    p = SUGGESTIONS_DIR / f"{slug_}.json"
    return json.loads(p.read_text()) if p.exists() else {"suggestions": []}


def save_suggestions(slug_, data):
    SUGGESTIONS_DIR.mkdir(parents=True, exist_ok=True)
    (SUGGESTIONS_DIR / f"{slug_}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_speaker_fixes(slug_):
    p = SPEAKER_FIXES_DIR / f"{slug_}.json"
    return json.loads(p.read_text()) if p.exists() else {"fixes": []}


def save_speaker_fixes(slug_, data):
    SPEAKER_FIXES_DIR.mkdir(parents=True, exist_ok=True)
    (SPEAKER_FIXES_DIR / f"{slug_}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", help="e.g. salon-020")
    ap.add_argument("file", type=Path, help="the edited TXT file")
    ap.add_argument("--by", default="", help="editor's name, credited on the text suggestions")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, write nothing")
    args = ap.parse_args()

    if not args.file.exists():
        sys.exit(f"not found: {args.file}")
    sessions = json.loads(CORPUS_JSON.read_text())
    session = next((s for s in sessions if slug(s) == args.slug), None)
    if not session:
        sys.exit(f"{args.slug} is not in {CORPUS_JSON}")
    segments = session["segments"]

    edited_turns = parse_edited_file(args.file)
    if len(edited_turns) != len(segments):
        sys.exit(f"Turn count mismatch: the edited file has {len(edited_turns)} turns, "
                  f"{label(session)} currently has {len(segments)} segments. A turn may have been added, "
                  f"removed, split or merged -- stopping without changing anything (see the module docstring).")
    drifted = [(i, et[0], seg["start"]) for i, (et, seg) in enumerate(zip(edited_turns, segments))
               if seg.get("start") is not None and abs(et[0] - seg["start"]) > DRIFT_TOLERANCE]
    if drifted:
        lines = "\n".join(f"  turn {i + 1}: file says {et}s, corpus says {orig}s" for i, et, orig in drifted[:10])
        sys.exit(f"{len(drifted)} turn(s) have a timecode that drifted more than {DRIFT_TOLERANCE:g}s from the "
                  f"original -- stopping without changing anything, since position-matching can no longer be "
                  f"trusted past this point:\n{lines}")

    text_changes, speaker_changes, para_mismatches = [], [], []
    for i, ((t, ed_speaker, ed_text), seg) in enumerate(zip(edited_turns, segments)):
        start = seg.get("start") or 0
        orig_speaker = seg.get("speaker")
        orig_display = orig_speaker or ("Transcript" if not any(s.get("speaker") for s in segments) else "Unattributed")
        if ed_speaker != orig_display and not (ed_speaker in PLACEHOLDER_SPEAKERS and not orig_speaker):
            speaker_changes.append((start, orig_display, ed_speaker))
        orig_paras, ed_paras = split_paragraphs(seg.get("text") or ""), split_paragraphs(ed_text)
        if len(orig_paras) != len(ed_paras):
            if orig_paras != ed_paras:   # only worth flagging if something actually differs
                para_mismatches.append((i + 1, start, len(orig_paras), len(ed_paras)))
            continue
        for old, new in zip(orig_paras, ed_paras):
            if old != new:
                text_changes.append((start, old, new))

    print(f"{label(session)}: {len(text_changes)} paragraph text change(s), {len(speaker_changes)} speaker "
          f"correction(s), {len(para_mismatches)} turn(s) with a changed paragraph count (text skipped for those)")
    for start, before, after in speaker_changes:
        print(f"  SPEAKER at {int(start)}s: {before!r} -> {after!r}")
    for i, start, n_old, n_new in para_mismatches:
        print(f"  SKIPPED turn {i} at {int(start)}s: paragraph count changed ({n_old} -> {n_new}); "
              f"apply this one by hand in TextReview if it's a real correction")
    for start, old, new in text_changes:
        preview_old = old if len(old) <= 70 else old[:67] + "..."
        preview_new = new if len(new) <= 70 else new[:67] + "..."
        print(f"  TEXT at {int(start)}s: {preview_old!r} -> {preview_new!r}")

    if args.dry_run:
        print("DRY RUN: nothing written.")
        return
    if not text_changes and not speaker_changes:
        print("Nothing to apply.")
        return

    if text_changes:
        store = load_suggestions(args.slug)
        now = time.strftime("%Y-%m-%d %H:%M")
        for start, old, new in text_changes:
            sid = hashlib.sha1(f"import-edited|{args.slug}|{int(start)}|{new}".encode()).hexdigest()[:10]
            if any(s["sid"] == sid for s in store["suggestions"]):
                continue
            store["suggestions"].append({
                "sid": sid, "slug": args.slug, "t": start, "old": old, "new": new,
                "by": args.by, "credit": bool(args.by), "note": "", "submitted": now,
                "source_id": "", "match": "exact", "status": "pending", "count": 1, "flag": ""})
        save_suggestions(args.slug, store)
        print(f"Filed {len(text_changes)} text suggestion(s) in raw/suggestions/{args.slug}.json "
              f"-- review in TextReview (python3 scripts/textreview.py) before they go live.")

    if speaker_changes:
        fixes = load_speaker_fixes(args.slug)
        now = time.strftime("%Y-%m-%d %H:%M")
        for start, _before, after in speaker_changes:
            fixes["fixes"] = [f for f in fixes["fixes"] if abs(f["t"] - start) > 1.5]
            fixes["fixes"].append({"t": round(start, 1), "speaker": after, "override": True, "at": now, "by": args.by})
        save_speaker_fixes(args.slug, fixes)
        print(f"Wrote {len(speaker_changes)} speaker correction(s) to data/speaker-fixes/{args.slug}.json "
              f"(review the SPEAKER lines above, then run: python3 scripts/05-build-corpus.py {args.slug})")


if __name__ == "__main__":
    main()
