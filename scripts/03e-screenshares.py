#!/usr/bin/env python3
"""Stage 3e -- screen shares: where each one starts and who is sharing.

Reads the Nametag readings (raw/nametag/<slug>.json, Stage 3c). In Zoom's recorded speaker view, while someone shares
their screen the shared content fills the frame and the current speaker's name sits on a small thumbnail top right;
Stage 3c marks those samples "share". A run of "share" samples is one screen share (brief gaps -- a missed reading, a
flicker -- are bridged). The sharer is the person whose name shows most often in the thumbnail during the run (the
sharer nearly always talks through their own share), matched to the names already known for the session (its
speakers and moderator), so spellings agree with the rest of the page.

Writes data/screenshares/<slug>.json, which Stage 6 shows under the participants list. Nothing here is guessed
without a video reading, and a session with no readings gets no file (and no section).

    python3 scripts/03e-screenshares.py salon-092
"""
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAP = 30          # seconds of missing "share" readings bridged inside one share
MIN_LEN = 20      # a run shorter than this is a flicker, not a share
MATCH = 0.72      # how close a name reading must be to a known name to count for it


def norm(s):
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def best_candidate(reading, candidates):
    scored = [(difflib.SequenceMatcher(None, norm(reading), norm(c)).ratio(), c) for c in candidates]
    score, name = max(scored)
    return name if score >= MATCH else None


def shares_for(slug):
    data = json.load(open(ROOT / "raw" / "nametag" / f"{slug}.json"))
    if not data.get("usable"):
        return None
    entry = next((e for e in json.load(open(ROOT / "corpus" / "corpus.json")) if e.get("video_id") == data["video_id"]), None)
    if not entry:
        return None
    candidates = [s["name"] for s in entry.get("speakers") or []]
    for extra in (entry.get("moderator"), entry.get("interviewer")):
        if extra and extra not in candidates:
            candidates.append(extra)
    runs, cur = [], None
    for t, name, layout in data["samples"]:
        if layout != "share":
            continue
        if cur and t - cur["end"] <= GAP:
            cur["end"] = t
        else:
            cur = {"start": t, "end": t, "names": Counter()}
            runs.append(cur)
        if name:
            who = best_candidate(name, candidates)
            if who:
                cur["names"][who] += 1
    out = []
    for r in runs:
        if r["end"] - r["start"] < MIN_LEN or not r["names"]:
            continue
        out.append({"start": r["start"], "end": r["end"] + data.get("step", 2), "name": r["names"].most_common(1)[0][0],
                    "readings": dict(r["names"].most_common())})
    return {"video_id": data["video_id"], "source": "nametag screen-share layout + on-screen name", "shares": out}


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python3 scripts/03e-screenshares.py <slug> [...]")
    out_dir = ROOT / "data" / "screenshares"
    out_dir.mkdir(exist_ok=True)
    for slug in sys.argv[1:]:
        res = shares_for(slug)
        if not res or not res["shares"]:
            print(f"{slug}: no screen shares found")
            continue
        (out_dir / f"{slug}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
        print(f"{slug}: {len(res['shares'])} screen shares")
        for s in res["shares"]:
            print(f"  {s['start'] // 60}:{s['start'] % 60:02d}-{s['end'] // 60}:{s['end'] % 60:02d}  {s['name']}  {s['readings']}")


if __name__ == "__main__":
    main()
