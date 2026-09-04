#!/usr/bin/env python3
"""Batch-fetch YouTube caption files (human subs + auto-subs, vtt) for every
Salon video. Read-only against YouTube; writes only caption files, no video,
no decision logic about which source to prefer -- that's a separate step.

Usage:
    python3 scripts/fetch-all-captions.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "raw" / "salon_video_index.json"
CAPTIONS_DIR = ROOT / "raw" / "captions"
LOG_PATH = ROOT / "raw" / "caption_fetch_log.json"


def fetch_one(number, video_id):
    out_dir = CAPTIONS_DIR / f"salon-{int(number):03d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    # skip if we already have a vtt for this video (idempotent re-runs)
    existing = list(out_dir.glob(f"{video_id}*.vtt"))
    if existing:
        return {"status": "skipped_existing", "files": [f.name for f in existing]}

    cmd = [
        "yt-dlp",
        "--write-auto-subs",
        "--write-subs",
        "--sub-langs", "en",
        "--skip-download",
        "--sub-format", "vtt",
        "-o", str(out_dir / "%(id)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    files = list(out_dir.glob(f"{video_id}*.vtt"))
    if files:
        return {"status": "ok", "files": [f.name for f in files]}
    if "no subtitles" in result.stdout.lower() or "no automatic captions" in result.stdout.lower():
        return {"status": "no_captions_available", "files": []}
    return {"status": "error", "stderr": result.stderr[-500:], "stdout": result.stdout[-500:]}


def main():
    with open(INDEX_PATH) as f:
        index = json.load(f)

    log = {}
    total = len(index)
    for i, (number, entry) in enumerate(sorted(index.items(), key=lambda kv: int(kv[0])), 1):
        video_id = entry["video_id"]
        print(f"[{i}/{total}] Salon {number} ({video_id})...", end=" ", flush=True)
        try:
            result = fetch_one(number, video_id)
        except subprocess.TimeoutExpired:
            result = {"status": "timeout"}
        except Exception as e:
            result = {"status": "exception", "error": str(e)}
        log[number] = {"video_id": video_id, **result}
        print(result["status"])
        # be polite to YouTube -- small delay between requests
        time.sleep(1)

    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)

    counts = {}
    for entry in log.values():
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    print()
    print("=== Summary ===")
    for status, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {status}: {count}")
    print(f"\nWrote log to {LOG_PATH}")


if __name__ == "__main__":
    main()
