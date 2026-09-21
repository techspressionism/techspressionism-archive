#!/usr/bin/env python3
"""A compact view of one recording for writing its synopsis: who is in it and the opening words of its main turns, with their times.

    python3 scripts/synopsis-digest.py interview-004 [max_lines]

Output: title, date, people, then up to max_lines (default 60) turns spread evenly through the recording: [m:ss] Speaker: first words.
The synopsis itself is written by hand into data/synopses/<slug>.txt (status: draft until Colin has reviewed it).
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import textreview as tr  # noqa: E402


def stamp(t):
    t = int(t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def main():
    slug = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    corpus = json.loads((ROOT / "corpus" / "corpus.json").read_text())
    e = next(x for x in corpus if f"{x.get('type', 'salon')}-{int(x['number']):03d}" == slug)
    print(f"{slug}: {e.get('session_title')} | {e.get('date_recorded')} | {e.get('duration_seconds')}s | series: {e.get('series') or e.get('type')}")
    print("interviewee:", e.get("interviewee"), "| interviewer:", e.get("interviewer"), "| moderator:", e.get("moderator"))
    print("people:", "; ".join(f"{s['name']} ({s.get('country') or ''})" for s in e.get("speakers") or []))
    blocks = tr.parse_corpus(slug)
    rows = []
    for b in blocks:
        txt = " ".join(b["paras"])
        n = len(txt.split())
        if n >= 30 and b["speaker"].lower() != "unattributed":
            rows.append((b["start"], b["speaker"], txt))
    if len(rows) > limit:
        step = len(rows) / limit
        rows = [rows[int(i * step)] for i in range(limit)]
    for t, sp, txt in rows:
        print(f"[{stamp(t)}] {sp.split()[0] if sp else '?'}: {txt[:230]}")


if __name__ == "__main__":
    main()
