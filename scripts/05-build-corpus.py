#!/usr/bin/env python3
"""Stage 5 -- assemble corpus/salon-NNN.md (+ corpus/corpus.json).

Two very different inputs depending on transcript_source:

- zoom-transcript: raw/transcripts_corrected/salon-NNN.json cues already
  carry per-utterance speaker attribution and Stage 4 corrections. Just
  normalize Zoom's raw speaker labels against Stage 1's speaker index /
  the artist index, then group consecutive same-speaker cues into blocks.

- youtube-*-captions: the Stage 2 output has no speaker structure at all
  (one flat word-timestamp stream). Slice it into per-speaker segments
  using Stage 1's manual speaker-index timestamps FIRST, then run name
  correction on each resulting segment -- deliberately not reusing Stage
  4's whole-transcript output here, since correcting the flat blob before
  slicing would leave word-level timestamps out of sync with text whose
  length Stage 4's substitutions may have changed.

Usage:
    python3 scripts/05-build-corpus.py 20 48 109
"""
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path

from lib_corrections import correct_text

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_PATH = ROOT / "data" / "sessions.json"
ARTISTS_PATH = ROOT / "data" / "artists.json"
VOCAB_PATH = ROOT / "data" / "vocabulary.json"
TRANSCRIPTS_DIR = ROOT / "raw" / "transcripts"
CORRECTED_DIR = ROOT / "raw" / "transcripts_corrected"
CORPUS_DIR = ROOT / "corpus"
REVIEW_CSV = ROOT / "review" / "name-candidates.csv"

NORMALIZE_THRESHOLD = 0.6
AUDIO_SHARED_PREFIX = "Audio shared by "


def seconds_to_display(total):
    if total is None:
        return "00:00"
    m, s = divmod(int(total), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def normalize_speaker_name(raw_name, session_speakers, artists):
    """Zoom's own speaker label is the participant's self-identification --
    not ASR output -- so it's already high-confidence and should mostly be
    left alone. Only normalize against the session's OWN Stage 1 speaker
    list (a small, scoped, strong prior: we know exactly who's presenting
    in this specific session). Do NOT fall back to fuzzy-matching against
    the full ~460-person artist index: that pool is unscoped, and two
    different real people can easily share enough of a name to collide
    (this once turned the real "Lisa Sutton" into a wrong match on the
    unrelated real artist "Lisa Scadron" -- a misattribution, not a typo
    fix, in a citable archive)."""
    if raw_name.startswith(AUDIO_SHARED_PREFIX):
        raw_name = raw_name[len(AUDIO_SHARED_PREFIX):]

    best = (0.0, None)
    for name in [s["name"] for s in session_speakers]:
        ratio = SequenceMatcher(None, raw_name.lower(), name.lower()).ratio()
        if raw_name.lower() in name.lower() or name.lower() in raw_name.lower():
            ratio = max(ratio, 0.75)
        if ratio > best[0]:
            best = (ratio, name)

    return best[1] if best[0] >= NORMALIZE_THRESHOLD else raw_name


def yaml_scalar(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if any(c in s for c in ':"#[]{}') or s != s.strip():
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def build_frontmatter(session, segments):
    lines = ["---"]
    lines.append(f"type: {session['type']}")
    lines.append(f"number: {session['number']}")
    lines.append(f"title: {yaml_scalar(session['session_title'])}")
    lines.append(f"date_recorded: {session['date_recorded'] or 'null'}")
    lines.append(f"date_published: {session['date_published'] or 'null'}")
    lines.append(f"video_id: {yaml_scalar(session['video_id'])}")
    lines.append(f"url: {yaml_scalar(session['url'])}")
    lines.append(f"duration_seconds: {session['duration_seconds']}")
    lines.append(f"moderator: {yaml_scalar(session['moderator'])}")
    lines.append("speakers:")
    for s in session.get("speakers", []):
        lines.append(f"  - name: {yaml_scalar(s['name'])}")
        lines.append(f"    country: {yaml_scalar(s['country'])}")
        lines.append(f"    start: {s['start_seconds']}")
    lines.append(f"transcript_source: {yaml_scalar(session['transcript_source'])}")
    lines.append('languages: ["en"]')
    if session.get("flags"):
        lines.append("flags:")
        for flag in session["flags"]:
            lines.append(f"  - {flag}")
    lines.append("---")
    return "\n".join(lines)


def build_body(session, segments):
    parts = []
    for seg in segments:
        ts = seconds_to_display(seg["start"])
        link = f"{session['url']}&t={int(seg['start'])}s" if seg["start"] is not None else session["url"]
        speaker = seg["speaker"] or "Unattributed"
        parts.append(f"## {speaker} [{ts}]({link})\n\n{seg['text']}")
    return "\n\n".join(parts)


def segments_from_zoom(session, artists):
    path = CORRECTED_DIR / f"salon-{int(session['number']):03d}.json"
    with open(path) as f:
        data = json.load(f)

    segments = []
    for cue in data["cues"]:
        speaker = normalize_speaker_name(cue["speaker"], session.get("speakers", []), artists) if cue["speaker"] else None
        if segments and segments[-1]["speaker"] == speaker:
            segments[-1]["text"] += " " + cue["text"]
            segments[-1]["end"] = cue["end"]
        else:
            segments.append({"speaker": speaker, "start": cue["start"], "end": cue["end"], "text": cue["text"]})
    return segments


def segments_from_youtube(session, artists, vocab_terms, review_rows):
    path = TRANSCRIPTS_DIR / f"salon-{int(session['number']):03d}.json"
    with open(path) as f:
        data = json.load(f)
    words = data.get("words") or []
    if not words:
        return []

    speaker_index = sorted(session.get("speakers", []), key=lambda s: s["start_seconds"])
    boundaries = [(s["start_seconds"], s["name"]) for s in speaker_index]

    segments = []
    if not boundaries:
        # No speaker index to slice by (Stage 1 flagged speaker_index_missing)
        # -- emit the whole transcript as one unattributed block rather than
        # silently dropping real transcript content from the corpus.
        segments.append({"speaker": None, "start": words[0]["start"], "boundary_end": None})
    elif boundaries[0][0] > words[0]["start"] + 5 and session.get("moderator"):
        segments.append({"speaker": session["moderator"], "start": words[0]["start"], "boundary_end": boundaries[0][0]})
    for i, (start, name) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else None
        segments.append({"speaker": name, "start": start, "boundary_end": end})

    result = []
    for seg in segments:
        seg_words = [w["text"] for w in words if w["start"] >= seg["start"] and (seg["boundary_end"] is None or w["start"] < seg["boundary_end"])]
        if not seg_words:
            continue
        text = " ".join(seg_words)
        text, _ = correct_text(text, session, artists, vocab_terms, session["number"], seg["start"], review_rows, [])
        result.append({"speaker": seg["speaker"], "start": seg["start"], "end": seg["boundary_end"], "text": text})
    return result


def process_session(session, artists, vocab_terms, review_rows):
    source = session.get("transcript_source")
    if source == "zoom-transcript":
        segments = segments_from_zoom(session, artists)
    elif source in ("youtube-auto-captions", "youtube-subtitles"):
        segments = segments_from_youtube(session, artists, vocab_terms, review_rows)
    else:
        print(f"Salon {session['number']}: no usable transcript source ({source}), skipping corpus build")
        return None

    frontmatter = build_frontmatter(session, segments)
    body = build_body(session, segments)
    md = f"{frontmatter}\n\n{body}\n"

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CORPUS_DIR / f"salon-{int(session['number']):03d}.md"
    out_path.write_text(md)
    return out_path, segments


def main():
    with open(SESSIONS_PATH) as f:
        sessions = json.load(f)
    with open(ARTISTS_PATH) as f:
        artists = json.load(f)
    with open(VOCAB_PATH) as f:
        vocab_terms = json.load(f)["terms"]

    numbers = set(sys.argv[1:]) if len(sys.argv) > 1 else None
    review_rows = []
    corpus_json = []

    for session in sessions:
        if numbers is not None and str(session["number"]) not in numbers:
            continue
        result = process_session(session, artists, vocab_terms, review_rows)
        if result:
            out_path, segments = result
            print(f"Salon {session['number']}: {len(segments)} segments -> {out_path}")
            corpus_json.append({**{k: session[k] for k in [
                "type", "number", "session_title", "date_recorded", "date_published",
                "video_id", "url", "duration_seconds", "moderator", "transcript_source", "flags",
            ]}, "segments": segments})

    corpus_json_path = CORPUS_DIR / "corpus.json"
    existing = []
    if corpus_json_path.exists():
        with open(corpus_json_path) as f:
            existing = json.load(f)
    by_number = {e["number"]: e for e in existing}
    for entry in corpus_json:
        by_number[entry["number"]] = entry
    with open(corpus_json_path, "w") as f:
        json.dump(sorted(by_number.values(), key=lambda e: e["number"]), f, indent=2, ensure_ascii=False)
    print(f"\nWrote {corpus_json_path}")

    if review_rows:
        import csv
        REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
        file_exists = REVIEW_CSV.exists()
        with open(REVIEW_CSV, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["session", "candidate_name", "matched_text", "similarity", "context", "cue_start"])
            if not file_exists:
                writer.writeheader()
            for row in review_rows:
                writer.writerow(row)
        print(f"{len(review_rows)} additional candidates written to {REVIEW_CSV}")


if __name__ == "__main__":
    main()
