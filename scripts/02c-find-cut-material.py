#!/usr/bin/env python3
"""Stage 2c -- find the Zoom speech that was edited out of the YouTube video.

When the raw Zoom recording is cut before upload, the Zoom transcript still
holds the removed speech, and a page built from it would print words that are
not in the video. Stage 2b's alignment shows each cut as a step in the offset
between the Zoom and video timelines: a step of D seconds means D seconds of
Zoom time were removed somewhere between the two anchors either side of it.
This stage finds which utterances they were.

    python3 scripts/02c-find-cut-material.py salon-094 salon-090
    python3 scripts/02c-find-cut-material.py            # every recording with a timeline file

For each cut of at least CUT_MIN seconds it looks at the utterances between the
two anchors and picks the contiguous run, about D seconds long, that best
explains the data: utterances before the run should appear in YouTube's captions
at the earlier offset, those after it at the later offset, and those inside it
at neither. That bounds the damage -- at most about D seconds of transcript are
removed per cut, never "everything that failed to match".

Writes raw/timeline/<slug>.cut.json (times only) which Stage 2 applies, and a
human-readable report at raw/cut-material-report.md. The report holds the text
that was cut, so it stays in raw/ (gitignored) and must never be committed.
A cut whose evidence is weak is marked "review" and is NOT applied.
"""
import glob
import importlib.util
import json
import re
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path

from lib_media import label, selected, slug

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TIMELINE_DIR = ROOT / "raw" / "timeline"
REPORT = ROOT / "raw" / "cut-material-report.md"
CUT_MIN = 6.0       # seconds; smaller steps are timing noise, not edits
PAD = 6.0           # seconds of slack when looking for an utterance in the captions
SPAN_SLACK = (0.25, 4.0)   # the run's length may differ from the cut by this fraction, or this many seconds
MAX_INSIDE = 0.35   # a run whose utterances match the video this well is not a cut
MIN_OUTSIDE = 0.45  # ...and its neighbours must match the video at least this well


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / name)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    spec.loader.exec_module(mod)
    return mod


def words_of(text):
    return re.findall(r"[a-z']+", text.lower())


def match_fraction(cue_words, yt_times, yt_words, a, b):
    """Share of the utterance's words found among YouTube's words in [a, b]."""
    if not cue_words:
        return None
    lo, hi = bisect_left(yt_times, a - PAD), bisect_right(yt_times, b + PAD)
    window = set(yt_words[lo:hi])
    return sum(1 for w in cue_words if w in window) / len(cue_words)


def find_cuts(cues, kept, yt_times, yt_words):
    """[{start, end, delta, ...}] in original Zoom time, one per cut."""
    out = []
    for (z1, d1), (z2, d2) in zip(kept, kept[1:]):
        delta = d1 - d2
        if delta < CUT_MIN:
            continue
        idx = [i for i, c in enumerate(cues) if c["start"] >= z1 - 2 and (c.get("end") or c["start"]) <= z2 + 2]
        if not idx:
            continue
        info = {}
        for i in idx:
            c = cues[i]
            e = c.get("end") or c["start"] + 2
            w = words_of(c["text"])
            info[i] = (max(len(w), 1), match_fraction(w, yt_times, yt_words, c["start"] + d1, e + d1) or 0.0,
                       match_fraction(w, yt_times, yt_words, c["start"] + d2, e + d2) or 0.0)
        best = None
        for a in range(len(idx)):
            for b in range(a, len(idx)):
                start, end = cues[idx[a]]["start"], (cues[idx[b]].get("end") or cues[idx[b]]["start"])
                length = end - start
                if length > delta * (1 + SPAN_SLACK[0]) + SPAN_SLACK[1]:
                    break
                if length < delta * (1 - SPAN_SLACK[0]) - SPAN_SLACK[1]:
                    continue
                cost = 0.0
                for k, i in enumerate(idx):
                    n, m1, m2 = info[i]
                    cost += n * (max(m1, m2) if a <= k <= b else (1 - m1) if k < a else (1 - m2))
                if best is None or cost < best[0]:
                    best = (cost, a, b)
        if best is None:
            out.append({"start": z1, "end": z2, "delta": round(delta, 1), "cues": [], "status": "review",
                        "why": "no run of utterances about the size of the cut"})
            continue
        _, a, b = best
        inside = [info[idx[k]] for k in range(a, b + 1)]
        before, after = [info[idx[k]] for k in range(a)], [info[idx[k]] for k in range(b + 1, len(idx))]
        wmean = lambda rows, f: sum(r[0] * f(r) for r in rows) / max(sum(r[0] for r in rows), 1)
        inside_m = wmean(inside, lambda r: max(r[1], r[2]))
        outside_rows = [(r[0], r[1]) for r in before] + [(r[0], r[2]) for r in after]
        outside_m = sum(n * m for n, m in outside_rows) / max(sum(n for n, _ in outside_rows), 1)
        ok = inside_m <= MAX_INSIDE and outside_m >= MIN_OUTSIDE
        out.append({
            "start": cues[idx[a]]["start"], "end": cues[idx[b]].get("end") or cues[idx[b]]["start"],
            "delta": round(delta, 1), "cues": [idx[k] for k in range(a, b + 1)],
            "inside_match": round(inside_m, 2), "outside_match": round(outside_m, 2),
            "status": "ok" if ok else "review",
        })
    return out


def main():
    args = sys.argv[1:]
    a2b = _load("02b-align-zoom-timeline.py")
    s2 = a2b._stage2()
    inventory = json.load(open(ROOT / "data" / "local-video-inventory.json"))
    lines = ["# Cut material (text in the Zoom transcript that is not in the YouTube video)", "",
             "Contains text that was deliberately cut from the videos. Do not commit or publish this file.", ""]
    for s in [s for s in json.load(open(ROOT / "data" / "sessions.json")) if s.get("transcript_source") == "zoom-transcript"]:
        if not selected(s, args) or not (TIMELINE_DIR / f"{slug(s)}.json").exists():
            continue
        sl = slug(s)
        zoom_path = s2.find_local_zoom_transcript(s, inventory)
        vtts = sorted(glob.glob(str(ROOT / "raw" / "captions" / sl / "*.vtt")))
        whisper_path = ROOT / "raw" / "whisper" / f"{sl}.json"
        if not (zoom_path and zoom_path.exists() and (vtts or whisper_path.exists())):
            print(f"{label(s)}: need the Zoom transcript file and either a Whisper transcript or YouTube captions -- skipped")
            continue
        cues = s2.parse_zoom_transcript(zoom_path.read_text(errors="replace"))
        if whisper_path.exists():        # the same reference Stage 2b uses: exact words on the video's own timeline
            yt = [(w["start"], w["text"]) for w in json.loads(whisper_path.read_text())["words"]]
        else:
            _, _, yt = s2.parse_youtube_captions(Path(vtts[0]).read_text(errors="replace"))
        yt = [(t, re.sub(r"[^a-z']", "", w.lower())) for t, w in yt]
        yt = [(t, w) for t, w in yt if w]
        kept = a2b.keep_anchors(a2b.find_anchors(a2b.zoom_words(cues), yt))
        cuts = find_cuts(cues, kept, [t for t, _ in yt], [w for _, w in yt])
        (TIMELINE_DIR / f"{sl}.cut.json").write_text(json.dumps({"spans": [
            {k: c[k] for k in ("start", "end", "delta", "status")} for c in cuts]}))
        applied = [c for c in cuts if c["status"] == "ok"]
        removed = sum(c["end"] - c["start"] for c in applied)
        expected = sum(c["delta"] for c in cuts)
        print(f"{label(s)}: {len(cuts)} cuts of {expected / 60:.1f} min in total; "
              f"{len(applied)} located ({removed / 60:.1f} min of transcript removed), "
              f"{len(cuts) - len(applied)} left for review")
        lines += [f"## {label(s)} -- {s.get('session_title') or ''}", ""]
        for c in cuts:
            m, ss = divmod(int(c["start"]), 60)
            lines.append(f"- Zoom {m}:{ss:02d}, cut of {c['delta']:.0f} s -- **{c['status']}**"
                         + (f" ({c.get('why')})" if c.get("why") else
                            f" (removed utterances match the video {c['inside_match']:.0%}; neighbours {c['outside_match']:.0%})"))
            for i in c["cues"]:
                lines.append(f"    - {cues[i].get('speaker') or '?'}: {cues[i]['text'][:400]}")
        lines.append("")
    REPORT.write_text("\n".join(lines))
    print(f"report: {REPORT}")


if __name__ == "__main__":
    main()
