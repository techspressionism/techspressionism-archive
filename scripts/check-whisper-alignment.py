#!/usr/bin/env python3
"""Do the Whisper timestamps line up with the YouTube video?

Whisper is fed audio whose length matches the YouTube video (Stage 3), but a
matching length doesn't prove the timelines agree -- the audio could be a cut
of the same length, or offset. This compares what Whisper and YouTube's own
captions say in the same 40-second windows spread across the recording
(overlap of word pairs), against the same test with the captions shifted five
minutes as a control. Aligned recordings score ~0.7-0.9 against ~0.02.

    python3 scripts/check-whisper-alignment.py                 # every raw/whisper/*.json
    python3 scripts/check-whisper-alignment.py salon-094 ...

Exit status 1 if any recording is not ALIGNED. No YouTube captions -> skipped.
"""
import glob
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WINDOWS, WINDOW_SECONDS, SHIFT_SECONDS = 13, 40, 300


def _stage2():
    spec = importlib.util.spec_from_file_location("stage2", HERE / "02-transcripts.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    spec.loader.exec_module(mod)
    return mod


def _pairs(words):
    return set(zip(words, words[1:]))


def _window(words, a, b):
    return re.findall(r"[a-z']+", " ".join(w for t, w in words if a <= t < b).lower())


def check(stage2, slug):
    vtts = sorted(glob.glob(str(ROOT / "raw" / "captions" / slug / "*.vtt")))
    path = ROOT / "raw" / "whisper" / f"{slug}.json"
    if not vtts or not path.exists():
        return None
    _, _, youtube = stage2.parse_youtube_captions(Path(vtts[0]).read_text(errors="replace"))
    whisper = [(w["start"], w["text"]) for w in json.loads(path.read_text())["words"]]
    duration = max(t for t, _ in whisper)
    same, shifted = [], []
    for i in range(1, WINDOWS + 1):
        a = duration * i / (WINDOWS + 1)
        w = _pairs(_window(whisper, a, a + WINDOW_SECONDS))
        y = _pairs(_window(youtube, a, a + WINDOW_SECONDS))
        c0 = (a + SHIFT_SECONDS) % duration
        c = _pairs(_window(youtube, c0, c0 + WINDOW_SECONDS))
        if len(w) > 10 and len(y) > 10:
            same.append(len(w & y) / min(len(w), len(y)))
        if len(w) > 10 and len(c) > 10:
            shifted.append(len(w & c) / min(len(w), len(c)))
    if not same or not shifted:
        return None
    s, h = sum(same) / len(same), sum(shifted) / len(shifted)
    return s, h, (s > 0.4 and s > 4 * h)


def main():
    stage2 = _stage2()
    slugs = sys.argv[1:] or sorted(Path(p).stem for p in glob.glob(str(ROOT / "raw" / "whisper" / "*.json")))
    bad = 0
    print(f"{'recording':16}{'same-time':>10}{'shifted':>9}  verdict")
    for slug in slugs:
        r = check(stage2, slug)
        if r is None:
            print(f"{slug:16}{'-':>10}{'-':>9}  skipped (no YouTube captions to compare)")
            continue
        s, h, ok = r
        bad += not ok
        print(f"{slug:16}{s:>10.2f}{h:>9.2f}  {'ALIGNED' if ok else 'NOT ALIGNED -- do not trust the timestamps'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
