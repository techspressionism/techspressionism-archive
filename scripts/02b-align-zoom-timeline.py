#!/usr/bin/env python3
"""Stage 2b -- put a Zoom transcript on the YouTube video's timeline.

A Zoom transcript is timed against the raw Zoom recording. The YouTube video
is often an edit of it (trimmed start/end, cuts), so Zoom timestamps can land
on the wrong moment of the video and every "watch" link built from them is off.
YouTube's captions are on the video's own timeline, so this finds phrases that
appear in both, and from them works out how far each stretch of the Zoom
transcript has to move.

    python3 scripts/02b-align-zoom-timeline.py salon-094 salon-103
    python3 scripts/02b-align-zoom-timeline.py            # every Zoom-transcript recording

Writes raw/timeline/<slug>.json ({"shifts": [[zoom_start_seconds, shift_seconds], ...]},
a step function: from each zoom_start onward, add shift). Stage 2 applies it.
A recording with no shift needed gets an empty list. Recordings whose alignment
cannot be established (no captions, too few matches) are reported, not guessed.
"""
import glob
import importlib.util
import json
import re
import statistics
import sys
from bisect import bisect_right
from pathlib import Path

from lib_media import label, selected, slug

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_DIR = ROOT / "raw" / "timeline"
N = 4                 # words per matching phrase
MIN_ANCHORS = 40      # fewer matches than this and the result is not trusted
TOLERANCE = 4.0       # seconds: anchors this close to the local median agree with each other
MIN_STEP = 3.0        # ignore shifts smaller than this (noise); a real edit moves things by more


def _stage2():
    spec = importlib.util.spec_from_file_location("stage2", HERE / "02-transcripts.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    spec.loader.exec_module(mod)
    return mod


def zoom_words(cues):
    """(time, word) with each cue's words spread across the cue."""
    out = []
    for c in cues:
        ws = re.findall(r"[a-z']+", c["text"].lower())
        end = c.get("end") or c["start"] + max(len(ws) * 0.35, 1)
        for i, w in enumerate(ws):
            out.append((c["start"] + (end - c["start"]) * i / max(len(ws), 1), w))
    return out


def phrases(words):
    """phrase -> list of times, for every N-word phrase."""
    d = {}
    for i in range(len(words) - N + 1):
        d.setdefault(" ".join(w for _, w in words[i:i + N]), []).append(words[i][0])
    return d


def find_anchors(zoom, yt):
    zp, yp = phrases(zoom), phrases(yt)
    # a phrase only anchors if it is unique on both sides
    pts = sorted((zp[p][0], yp[p][0]) for p in zp if len(zp[p]) == 1 and len(yp.get(p, ())) == 1)
    # keep a monotonic chain (longest increasing subsequence on the YouTube time)
    tails, prev, idx = [], [-1] * len(pts), []
    for i, (_, y) in enumerate(pts):
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if pts[tails[mid]][1] < y:
                lo = mid + 1
            else:
                hi = mid
        if lo:
            prev[i] = tails[lo - 1]
        if lo == len(tails):
            tails.append(i)
        else:
            tails[lo] = i
    chain, i = [], tails[-1] if tails else -1
    while i != -1:
        chain.append(pts[i])
        i = prev[i]
    return chain[::-1]


def keep_anchors(anchors):
    """[(zoom_time, shift)] for the anchors that agree with their neighbours
    (a lone false match is dropped); shift = youtube time - zoom time."""
    deltas = [(z, y - z) for z, y in anchors]
    keep = []
    for k, (z, d) in enumerate(deltas):
        near = [x for _, x in deltas[max(0, k - 7):k + 8]]
        if abs(d - statistics.median(near)) <= TOLERANCE:
            keep.append((z, d))
    return keep


def step_function(anchors):
    """[[zoom_start, shift], ...] from the anchors' offsets (youtube - zoom)."""
    keep = keep_anchors(anchors)
    steps = []
    for z, d in keep:
        if not steps or abs(d - steps[-1][1]) > MIN_STEP:
            steps.append([round(z, 1), round(d, 1)])
    # the first step applies from the start of the recording
    if steps:
        steps[0][0] = 0.0
    return [s for s in steps if abs(s[1]) > MIN_STEP or len(steps) > 1]


def apply(steps, t):
    if not steps:
        return t
    i = bisect_right([s[0] for s in steps], t) - 1
    return t + steps[max(i, 0)][1]


def overlap_after(stage2, cues, yt_words, steps):
    """same-time overlap of the (shifted) Zoom words with the YouTube captions, 13 windows."""
    pairs = lambda w: set(zip(w, w[1:]))
    z = [(apply(steps, t), w) for t, w in zoom_words(cues)]
    dur = max(t for t, _ in yt_words)
    win = lambda ws, a, b: [w for t, w in ws if a <= t < b]
    scores = []
    for i in range(1, 14):
        a = dur * i / 14
        A, Y = pairs(win(z, a, a + 40)), pairs(win(yt_words, a, a + 40))
        if len(A) > 10 and len(Y) > 10:
            scores.append(len(A & Y) / min(len(A), len(Y)))
    return sum(scores) / len(scores) if scores else None


def main():
    args = sys.argv[1:]
    stage2 = _stage2()
    sessions = [s for s in json.load(open(ROOT / "data" / "sessions.json"))
                if s.get("transcript_source") == "zoom-transcript" and selected(s, args)]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for s in sessions:
        sl = slug(s)
        vtts = sorted(glob.glob(str(ROOT / "raw" / "captions" / sl / "*.vtt")))
        if not vtts:
            print(f"{label(s)}: no YouTube captions -- cannot align this way")
            continue
        cues = json.load(open(ROOT / "raw" / "transcripts" / f"{sl}.json"))["cues"]
        # Stage 2 may already have shifted these; always align from the original Zoom times
        cues = [{**c, "start": c.get("orig_start", c["start"]), "end": c.get("orig_end", c.get("end"))} for c in cues]
        _, _, yt = stage2.parse_youtube_captions(Path(vtts[0]).read_text(errors="replace"))
        yt_words = [(t, w) for t, w in ((t, re.sub(r"[^a-z']", "", w.lower())) for t, w in yt) if w]
        anchors = find_anchors(zoom_words(cues), yt_words)
        if len(anchors) < MIN_ANCHORS:
            print(f"{label(s)}: only {len(anchors)} matching phrases -- not trusted, no shift written")
            continue
        steps = step_function(anchors)
        if statistics.median(abs(d) for _, d in keep_anchors(anchors)) < 2.5:
            steps = []  # already on the video's timeline; what the step function found is timing noise
        before, after = overlap_after(stage2, cues, yt_words, []), overlap_after(stage2, cues, yt_words, steps)
        (OUT_DIR / f"{sl}.json").write_text(json.dumps({"anchors": len(anchors), "shifts": steps}))
        desc = "no shift needed" if not steps else ", ".join(f"from {z / 60:.0f}:{z % 60:02.0f} {d:+.0f}s" for z, d in steps)
        print(f"{label(s)}: {len(anchors)} matches; {desc}; overlap {before:.2f} -> {after:.2f}")


if __name__ == "__main__":
    main()
