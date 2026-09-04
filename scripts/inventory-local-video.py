#!/usr/bin/env python3
"""Inventory the local Salon recordings directory. Read-only -- lists every
file with extension and size, and flags which session folders have an
accompanying .vtt or .txt transcript already sitting alongside the media.

Usage:
    python3 scripts/inventory-local-video.py ~/Documents/~TECHSPRESSIONISM/VIDEO/SALON
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "data" / "local-video-inventory.json"
OUT_CSV = ROOT / "data" / "local-video-inventory.csv"

SALON_DIR_RE = re.compile(r"^SALON_(\d+)$", re.IGNORECASE)


def classify_file(name):
    """Zoom's local export naming distinguishes real speech transcripts from
    chat logs and unattributed closed captions -- don't conflate them.
      *.transcript.vtt / .txt / .pdf  -> real transcript, speaker-attributed
      *.cc.vtt                        -> closed captions, no speaker labels
      *newChat.txt / plain *_Recording.txt -> chat log, not speech at all
    """
    lower = name.lower()
    if ".transcript." in lower:
        return "transcript"
    if ".cc.vtt" in lower:
        return "closed_captions"
    if lower.endswith(".txt"):
        return "chat_log"
    return None


def human_size(n):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def inventory(base_dir):
    base_dir = Path(base_dir).expanduser()
    if not base_dir.is_dir():
        print(f"Not a directory: {base_dir}", file=sys.stderr)
        sys.exit(1)

    all_files = []
    sessions = {}  # salon number (str) -> {files: [...], has_vtt, has_txt}
    other_dirs = {}  # non-SALON_N top-level dirs -> files

    for top in sorted(base_dir.iterdir()):
        if top.name.startswith("."):
            continue
        m = SALON_DIR_RE.match(top.name)
        if top.is_dir() and m:
            number = m.group(1)
            bucket = sessions.setdefault(
                number,
                {
                    "dir_name": top.name,
                    "files": [],
                    "has_transcript": False,
                    "has_closed_captions": False,
                    "has_chat_log": False,
                },
            )
            for f in sorted(top.rglob("*")):
                if f.is_file() and not f.name.startswith("."):
                    ext = f.suffix.lower().lstrip(".")
                    size = f.stat().st_size
                    rel = str(f.relative_to(base_dir))
                    kind = classify_file(f.name)
                    entry = {"path": rel, "ext": ext, "size_bytes": size, "kind": kind}
                    bucket["files"].append(entry)
                    all_files.append(entry)
                    if kind == "transcript":
                        bucket["has_transcript"] = True
                    elif kind == "closed_captions":
                        bucket["has_closed_captions"] = True
                    elif kind == "chat_log":
                        bucket["has_chat_log"] = True
        elif top.is_dir():
            files = []
            for f in sorted(top.rglob("*")):
                if f.is_file() and not f.name.startswith("."):
                    ext = f.suffix.lower().lstrip(".")
                    size = f.stat().st_size
                    rel = str(f.relative_to(base_dir))
                    entry = {"path": rel, "ext": ext, "size_bytes": size}
                    other_dirs.setdefault(top.name, []).append(entry)
                    all_files.append(entry)
        elif top.is_file():
            ext = top.suffix.lower().lstrip(".")
            size = top.stat().st_size
            entry = {"path": top.name, "ext": ext, "size_bytes": size}
            other_dirs.setdefault("(loose files at top level)", []).append(entry)
            all_files.append(entry)

    return sessions, other_dirs, all_files


def main():
    if len(sys.argv) != 2:
        print("Usage: inventory-local-video.py <salon-directory>", file=sys.stderr)
        sys.exit(1)

    base_dir = sys.argv[1]
    sessions, other_dirs, all_files = inventory(base_dir)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(
            {
                "source_dir": str(Path(base_dir).expanduser()),
                "sessions": sessions,
                "other_dirs": other_dirs,
            },
            f,
            indent=2,
        )

    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["salon_number", "path", "ext", "size_bytes"])
        for number, bucket in sorted(sessions.items(), key=lambda kv: int(kv[0])):
            for entry in bucket["files"]:
                writer.writerow([number, entry["path"], entry["ext"], entry["size_bytes"]])
        for dirname, files in other_dirs.items():
            for entry in files:
                writer.writerow(["", entry["path"], entry["ext"], entry["size_bytes"]])

    # console report
    print(f"Salon session folders found: {len(sessions)}")
    print(f"Other top-level entries: {list(other_dirs.keys())}")
    print(f"Total files: {len(all_files)}")
    total_size = sum(e["size_bytes"] for e in all_files)
    print(f"Total size: {human_size(total_size)}")
    print()

    ext_counts = {}
    for e in all_files:
        ext_counts[e["ext"]] = ext_counts.get(e["ext"], 0) + 1
    print("File extensions across all files:")
    for ext, count in sorted(ext_counts.items(), key=lambda kv: -kv[1]):
        print(f"  .{ext or '(none)'}: {count}")
    print()

    with_real_transcript = [n for n, b in sessions.items() if b["has_transcript"]]
    cc_only = [
        n for n, b in sessions.items()
        if b["has_closed_captions"] and not b["has_transcript"]
    ]
    chat_log_only = [
        n for n, b in sessions.items()
        if b["has_chat_log"] and not b["has_transcript"] and not b["has_closed_captions"]
    ]
    nothing = [
        n for n, b in sessions.items()
        if not b["has_transcript"] and not b["has_closed_captions"] and not b["has_chat_log"]
    ]

    print(f"Sessions with a REAL speaker-attributed transcript (*.transcript.vtt/.txt/.pdf): {len(with_real_transcript)}")
    print(f"  {sorted(with_real_transcript, key=int)}")
    print()
    print(f"Sessions with ONLY unattributed closed captions (*.cc.vtt, no .transcript file): {len(cc_only)}")
    print(f"  {sorted(cc_only, key=int)}")
    print()
    print(f"Sessions with ONLY a Zoom chat log (no real transcript, no captions -- chat text is not speech): {len(chat_log_only)}")
    print(f"  {sorted(chat_log_only, key=int)}")
    print()
    print(f"Sessions with NOTHING transcript-shaped at all: {len(nothing)}")
    print(f"  {sorted(nothing, key=int)}")

    print()
    print(f"Wrote full inventory to {OUT_JSON}")
    print(f"Wrote flat CSV to {OUT_CSV}")


if __name__ == "__main__":
    main()
