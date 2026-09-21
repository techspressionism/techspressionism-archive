#!/usr/bin/env python3
"""Find the places where Whisper got stuck repeating a phrase ("I just need to get it out" x 30) in raw/whisper/<slug>.json.

    python3 scripts/find-whisper-loops.py                # every Whisper transcript: a summary table
    python3 scripts/find-whisper-loops.py interview-013  # one recording: each loop with its time and words

A LOOP is a phrase of 3-8 words repeated 4 or more times in a row, or of 1-2 words repeated 8 or more times. Returns spans in seconds.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHISPER = ROOT / "raw" / "whisper"


def norm(t):
    return re.sub(r"[^a-z0-9']", "", t.lower())


def loops(words):
    """[(first_index, last_index, phrase, repeats)] over a list of {'start','text'} words."""
    toks = [norm(w["text"]) for w in words]
    n_words = len(toks)
    found, i = [], 0
    while i < n_words:
        best = None
        for n in range(1, 9):
            need = 8 if n <= 2 else 4
            if i + n * need > n_words:
                continue
            gram = toks[i:i + n]
            if not any(gram):
                continue
            reps = 1
            while i + (reps + 1) * n <= n_words and toks[i + reps * n:i + (reps + 1) * n] == gram:
                reps += 1
            if reps >= need and (best is None or reps * n > best[3] * best[2]):
                best = (i, i + reps * n - 1, n, reps)
        if best:
            a, b, n, reps = best
            found.append((a, b, " ".join(w["text"] for w in words[a:a + n]), reps))
            i = b + 1
        else:
            i += 1
    return found


def spans(slug):
    d = json.loads((WHISPER / f"{slug}.json").read_text())
    words = d["words"]
    out = []
    for a, b, phrase, reps in loops(words):
        t0 = words[a]["start"]
        t1 = words[min(b + 1, len(words) - 1)]["start"]
        out.append({"start": t0, "end": t1, "phrase": phrase, "repeats": reps, "first": a, "last": b})
    return out


def main():
    if len(sys.argv) > 1:
        for s in spans(sys.argv[1]):
            print(f"{s['start']:8.1f}s-{s['end']:8.1f}s  x{s['repeats']:<3} {s['phrase']!r}")
        return
    rows = []
    for p in sorted(WHISPER.glob("*.json")):
        sp = spans(p.stem)
        if sp:
            rows.append((p.stem, len(sp), round(sum(x["end"] - x["start"] for x in sp)), sum(x["repeats"] for x in sp)))
    rows.sort(key=lambda r: -r[2])
    print(f"{len(rows)} Whisper transcripts with loops (of {len(list(WHISPER.glob('*.json')))})")
    print(f"{'recording':16} {'loops':>5} {'seconds':>8}")
    for r in rows:
        print(f"{r[0]:16} {r[1]:5d} {r[2]:8d}")
    print("total loops:", sum(r[1] for r in rows), " total seconds:", sum(r[2] for r in rows))


if __name__ == "__main__":
    main()
