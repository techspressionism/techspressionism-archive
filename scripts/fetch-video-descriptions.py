#!/usr/bin/env python3
"""Fetch the YouTube description of every recording that has no raw/video_json/<id>.json yet (metadata only, nothing is downloaded),
one at a time with a pause. Written to raw/video_desc_fetch/<id>.json. extract-video-descriptions.py reads both places.

    .venv/bin/python scripts/fetch-video-descriptions.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "raw" / "video_desc_fetch"
YTDLP = ROOT / ".venv" / "bin" / "yt-dlp"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sessions = json.load(open(ROOT / "data" / "sessions.json"))
    todo = [s["video_id"] for s in sessions if not (ROOT / "raw" / "video_json" / f"{s['video_id']}.json").exists() and not (OUT / f"{s['video_id']}.json").exists()]
    print(f"{len(todo)} descriptions to fetch", flush=True)
    for i, vid in enumerate(todo, 1):
        r = subprocess.run([str(YTDLP), "--skip-download", "--dump-single-json", "--no-warnings", f"https://www.youtube.com/watch?v={vid}"],
                           capture_output=True, text=True, timeout=180)
        try:
            d = json.loads(r.stdout)
            (OUT / f"{vid}.json").write_text(json.dumps({"id": vid, "description": d.get("description") or ""}, ensure_ascii=False))
            print(f"  {i}/{len(todo)} {vid} ok", flush=True)
        except Exception:
            print(f"  {i}/{len(todo)} {vid} FAILED: {(r.stderr or r.stdout)[:120]}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
