#!/usr/bin/env python3
"""One row per recording with the signs of trouble a machine can see, for review. Nothing is changed.

    python3 scripts/quality-report.py            # writes review/quality-report.csv

Per recording: transcript source, words, words per minute, share of words still Unattributed, and
  repeated       the same sentence repeated 3+ times in a row (a speech-recognition loop) or stock phantom phrases ("thanks for watching", "subtitles by")
  long gaps      stretches of 90+ seconds with no words at all (speech that was dropped, or a long silence)
  bracket marks  [Music] / [inaudible] style markers left in the text
  crosscheck     how much of the Whisper text agrees with the recording's other transcript (from 03-whisper-transcribe)
Sorted worst first. Use it to decide where a person should listen.
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from lib_sentences import split_sentences  # noqa: E402

PHANTOM = re.compile(r"thanks? for watching|thank you for watching|subtitles? by|amara\.org|please subscribe|like and subscribe|transcribed by", re.I)


def main():
    corpus = json.loads((ROOT / "corpus" / "corpus.json").read_text())
    rows = []
    for e in corpus:
        slug = f"{e.get('type', 'salon')}-{int(e['number']):03d}"
        segs = e["segments"]
        words = sum(len(s["text"].split()) for s in segs)
        un = sum(len(s["text"].split()) for s in segs if not s.get("speaker"))
        dur = e.get("duration_seconds") or 0
        sents = [x[2] for s in segs for para in s["text"].split("\n\n") for x in split_sentences(para)]
        loops, run = 0, 1
        for a, b in zip(sents, sents[1:]):
            run = run + 1 if a.strip().lower() == b.strip().lower() and len(a.split()) >= 3 else 1
            if run == 3:
                loops += 1
        phantom = sum(1 for s in sents if PHANTOM.search(s))
        starts = sorted(float(x) for s in segs for para in (s.get("sentence_times") or [[s.get("start")]]) for x in (para if isinstance(para, list) else [para])
                        if x is not None)              # the start of every sentence: a gap here is a stretch without words
        gaps = [(int(a), int(b)) for a, b in zip(starts, starts[1:] + [dur or starts[-1]]) if b - a >= 90]
        brackets = sum(len(re.findall(r"\[[^\]]{2,25}\]", s["text"])) for s in segs)
        cc = ""
        wp = ROOT / "raw" / "whisper" / f"{slug}.json"
        if wp.exists():
            cc = json.loads(wp.read_text()).get("crosscheck_overlap") or ""
        score = loops * 5 + phantom * 3 + len(gaps) * 2 + brackets // 5 + (2 if (cc and float(cc) < 0.6) else 0)
        rows.append([score, slug, e.get("transcript_source"), words, round(words / (dur / 60), 1) if dur else "", round(100 * un / max(1, words)),
                     loops, phantom, "; ".join(f"{a}-{b}s" for a, b in gaps[:6]), brackets, cc])
    rows.sort(key=lambda r: (-r[0], r[1]))
    out = ROOT / "review" / "quality-report.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["trouble score", "recording", "source", "words", "words per minute", "% unattributed", "repeated sentence loops", "phantom phrases",
                    "long gaps (90+ s without words)", "bracket markers", "crosscheck overlap"])
        w.writerows(rows)
    print(f"{len(rows)} recordings -> {out.relative_to(ROOT)}; trouble score above 0: {sum(1 for r in rows if r[0] > 0)}")


if __name__ == "__main__":
    main()
