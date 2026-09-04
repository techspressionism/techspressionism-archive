#!/usr/bin/env python3
"""Stage 2 -- select and clean a transcript source per session.

Priority: local Zoom transcript (speaker-attributed, per utterance) first,
YouTube captions (human subs > auto-captions) as fallback. Neither is
run through diarization -- Zoom already tags speakers per line; YouTube
captions come back as a single undifferentiated stream and get chopped
into per-speaker segments later (Stage 5), against Stage 1's manual
speaker index.

Text cleaning: "clean verbatim" style -- strip non-lexical fillers
("uh", "um") only. Everything else, including "like"/"you know", is
left exactly as spoken.

Usage:
    python3 scripts/02-transcripts.py 20 48 109
    python3 scripts/02-transcripts.py            # all sessions in data/sessions.json
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_PATH = ROOT / "data" / "sessions.json"
INVENTORY_PATH = ROOT / "data" / "local-video-inventory.json"
CAPTIONS_DIR = ROOT / "raw" / "captions"
OUT_DIR = ROOT / "raw" / "transcripts"
LOCAL_SALON_DIR = Path.home() / "Documents" / "~TECHSPRESSIONISM" / "VIDEO" / "SALON"

TIME_RE = re.compile(r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})")
TAG_RE = re.compile(r"<[^>]+>")
FILLER_RE = re.compile(r"\buh+\b[,]?\s*|\bum+\b[,]?\s*", re.IGNORECASE)
BRACKET_MARKER_RE = re.compile(r"\[[^\]]+\]")
SPEAKER_PREFIX_RE = re.compile(r"^([A-Za-z][\w .'\-]{0,60}):\s(.*)$", re.DOTALL)


def vtt_time_to_seconds(t):
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_webvtt_blocks(text):
    """Yield (start_seconds, end_seconds, raw_cue_text) for each timed cue,
    skipping header/index lines."""
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            m = TIME_RE.search(line)
            if m:
                start = vtt_time_to_seconds(m.group(1))
                end = vtt_time_to_seconds(m.group(2))
                cue_text = " ".join(lines[i + 1:]).strip()
                if cue_text:
                    yield start, end, cue_text
                break


def strip_fillers(text):
    cleaned = FILLER_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
    return cleaned


def parse_zoom_transcript(text):
    cues = []
    for start, end, raw in parse_webvtt_blocks(text):
        m = SPEAKER_PREFIX_RE.match(raw)
        if m:
            speaker, utterance = m.group(1).strip(), m.group(2).strip()
        else:
            speaker, utterance = None, raw
        utterance = strip_fillers(utterance)
        if utterance:
            cues.append({"start": round(start, 2), "end": round(end, 2), "speaker": speaker, "text": utterance})
    return cues


WORD_TAG_RE = re.compile(r"<(\d{2}:\d{2}:\d{2}\.\d{3})><c>([^<]*)</c>")
LEADING_TAG_RE = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")


def parse_youtube_captions(text):
    """YouTube auto-captions render as 'rolling' cues: each cue repeats some
    already-shown context as plain text, then reveals new words wrapped in
    per-word timestamp tags (<00:00:03.199><c>word</c>) as they're spoken.
    The window doesn't grow monotonically (it slides), so a naive
    prefix-diff against the previous cue under-deduplicates. Instead: trust
    every tagged word as new (each real word gets exactly one tag, when
    first revealed), and for each cue's untagged leading text, drop only
    the portion that overlaps the tail of what's already been captured."""
    is_auto = "Kind: captions" in "\n".join(text.splitlines()[:5])

    words = []  # (timestamp, word)
    accumulated_words = []  # flat word list captured so far, for overlap checks

    for start, end, raw in parse_webvtt_blocks(text):
        first_tag = LEADING_TAG_RE.search(raw)
        leading = raw[: first_tag.start()] if first_tag else raw
        tagged_part = raw[first_tag.start():] if first_tag else ""

        leading_plain = TAG_RE.sub("", leading).replace("\n", " ")
        leading_words = re.sub(r"\s+", " ", leading_plain).strip().split()

        overlap = 0
        max_check = min(len(leading_words), len(accumulated_words))
        for k in range(max_check, 0, -1):
            if accumulated_words[-k:] == leading_words[:k]:
                overlap = k
                break
        for w in leading_words[overlap:]:
            words.append((start, w))
            accumulated_words.append(w)

        for ts_str, word in WORD_TAG_RE.findall(tagged_part):
            w = word.strip()
            if w:
                ts = vtt_time_to_seconds(ts_str)
                words.append((ts, w))
                accumulated_words.append(w)

    # Drop standalone hesitation-sound tokens rather than regex-stripping the
    # joined string -- keeps filler removal exact at word boundaries and
    # preserves per-word timing, which Stage 5 needs to slice this flat,
    # speaker-less stream against Stage 1's manual speaker-index timestamps.
    filler_words = {"uh", "um"}
    words = [(ts, w) for ts, w in words if re.sub(r"[^\w]", "", w).lower() not in filler_words]

    full_text = " ".join(w for _, w in words)
    cues = [{"start": round(words[0][0], 2) if words else 0.0, "end": None, "speaker": None, "text": full_text}]
    return cues, is_auto, words


def compute_quality(cues, duration_seconds):
    full_text = " ".join(c["text"] for c in cues)
    word_count = len(full_text.split())
    duration_min = (duration_seconds or 0) / 60 or 1
    wpm = round(word_count / duration_min, 1)
    marker_count = len(BRACKET_MARKER_RE.findall(full_text))
    low_confidence = wpm < 60 or (word_count and marker_count / max(word_count, 1) > 0.05)
    return {
        "word_count": word_count,
        "duration_seconds": duration_seconds,
        "words_per_minute": wpm,
        "bracket_marker_count": marker_count,
        "low_confidence_flag": bool(low_confidence),
    }


def find_local_zoom_transcript(number, inventory):
    bucket = inventory.get("sessions", {}).get(str(int(number)))
    if not bucket:
        return None
    candidates = [f for f in bucket["files"] if f["kind"] == "transcript" and f["ext"] == "vtt"]
    if not candidates:
        return None
    return LOCAL_SALON_DIR / candidates[0]["path"]


def find_youtube_caption(number):
    d = CAPTIONS_DIR / f"salon-{int(number):03d}"
    if not d.is_dir():
        return None
    vtts = sorted(d.glob("*.vtt"))
    return vtts[0] if vtts else None


def process_session(session, inventory):
    number = session["number"]
    duration = session["duration_seconds"]

    words = None  # word-level (timestamp, word) list -- only populated for
    # the YouTube path, where cues has no per-speaker structure to slice by.
    zoom_path = find_local_zoom_transcript(number, inventory)
    if zoom_path and zoom_path.exists():
        text = zoom_path.read_text(errors="replace")
        cues = parse_zoom_transcript(text)
        source = "zoom-transcript"
    else:
        yt_path = find_youtube_caption(number)
        if yt_path and yt_path.exists():
            text = yt_path.read_text(errors="replace")
            cues, is_auto, words = parse_youtube_captions(text)
            source = "youtube-auto-captions" if is_auto else "youtube-subtitles"
        else:
            cues = []
            source = "none"

    quality = compute_quality(cues, duration) if cues else {
        "word_count": 0, "duration_seconds": duration, "words_per_minute": 0,
        "bracket_marker_count": 0, "low_confidence_flag": True,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"salon-{int(number):03d}.json"
    output = {"video_id": session["video_id"], "source": source, "cues": cues, "quality": quality}
    if words is not None:
        output["words"] = [{"start": round(ts, 2), "text": w} for ts, w in words]
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    session["transcript_source"] = source
    session["transcript_quality"] = quality
    if source == "none":
        if "no_transcript_source_available" not in session["flags"]:
            session["flags"].append("no_transcript_source_available")
    elif quality["low_confidence_flag"]:
        if "transcript_quality_low" not in session["flags"]:
            session["flags"].append("transcript_quality_low")

    return source, quality, out_path


def main():
    with open(SESSIONS_PATH) as f:
        sessions = json.load(f)
    with open(INVENTORY_PATH) as f:
        inventory = json.load(f)

    numbers = set(sys.argv[1:]) if len(sys.argv) > 1 else None

    for session in sessions:
        if numbers is not None and str(session["number"]) not in numbers:
            continue
        source, quality, out_path = process_session(session, inventory)
        print(f"Salon {session['number']}: source={source} words={quality['word_count']} "
              f"wpm={quality['words_per_minute']} low_confidence={quality['low_confidence_flag']} -> {out_path}")

    with open(SESSIONS_PATH, "w") as f:
        json.dump(sessions, f, indent=2, ensure_ascii=False)
    print(f"\nUpdated {SESSIONS_PATH}")


if __name__ == "__main__":
    main()
