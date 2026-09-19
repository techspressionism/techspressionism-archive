#!/usr/bin/env python3
"""Stage 3c -- Nametag: who is on screen as the speaker, second by second.

Zoom's speaker-view recording shows the current speaker's display name in a
small label (bottom-left of the frame, or bottom-left of the small thumbnail
in the top-right while someone shares their screen). This stage samples the
video every STEP seconds, reads that label with Apple's Vision framework (a
small Swift tool, compiled on first use), and writes the readings to
raw/nametag/<slug>.json. Stage 5 combines them with Whisper's word timestamps
to attribute each passage.

    .venv/bin/python scripts/03c-nametag.py salon-092 interview-005
    .venv/bin/python scripts/03c-nametag.py --force --max-minutes 15 salon-092   # quick trial

Works only on a speaker-view or screen-share recording. Gallery-view videos
don't mark who is speaking, and edited exports usually have no label, so a run
that reads a name in under MIN_LABEL_SHARE of the frames is written with
"usable": false and Stage 5 ignores it.

Reuses Stage 3's local-file lookup (folder rules + duration match).
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from lib_media import label, selected, slug

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SESSIONS_PATH = ROOT / "data" / "sessions.json"
OUT_DIR = ROOT / "raw" / "nametag"
BIN = OUT_DIR / "bin" / "ocr"
SWIFT_SRC = HERE / "nametag_ocr.swift"
WORK_DIR = OUT_DIR / "work"

STEP = 2                 # seconds between samples
WINDOW = 900             # seconds of video handled at a time (bounds temp disk use)
MIN_LABEL_SHARE = 0.25   # a video that yields a name in fewer samples than this has no usable labels
MIN_CONF = 0.5

# Crop boxes as fractions of the frame (measured on 2560x1440 Zoom recordings):
# top-right thumbnail while someone shares, bottom-left name label in speaker view.
THUMB = (0.875, 0.0, 0.125, 0.132)     # x, y, w, h
LABEL = (0.0, 0.958, 0.22, 0.042)


def _load_stage3():
    spec = importlib.util.spec_from_file_location("stage3", HERE / "03-whisper-transcribe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ensure_ocr_tool():
    if BIN.exists() and BIN.stat().st_mtime >= SWIFT_SRC.stat().st_mtime:
        return
    BIN.parent.mkdir(parents=True, exist_ok=True)
    print("compiling the text reader (needs the Command Line Tools' swiftc)...", flush=True)
    subprocess.run(["swiftc", "-O", str(SWIFT_SRC), "-o", str(BIN)], check=True)


def find_speaker_view_video(stage3, session):
    """The non-gallery .mp4 of the recording: the one whose duration matches."""
    duration = session.get("duration_seconds")
    found = []
    for root in stage3.local_search_roots(session):
        if not root.is_dir():
            continue
        for p in root.rglob("*.mp4"):
            if p.name.startswith(".") or "Premiere" in str(p) or "gallery" in p.name.lower():
                continue
            d = stage3.probe_duration(p)
            if d and duration and abs(d - duration) <= stage3.DURATION_TOLERANCE * duration + 2:
                found.append(("edit" in p.name.lower(), str(p)))
    return Path(sorted(found)[0][1]) if found else None


def ocr(paths):
    out = {}
    for i in range(0, len(paths), 60):
        r = subprocess.run([str(BIN), *map(str, paths[i:i + 60])], capture_output=True, text=True, check=True)
        for rec in json.loads(r.stdout):
            out[rec["f"]] = rec["items"]
    return out


def read_name(items, region):
    """The most name-like text in the part of the crop where the label sits."""
    ok = [i for i in items if i["c"] >= MIN_CONF and re.search(r"[A-Za-z]{2,}", i["t"])
          and (i["y"] > 0.5 if region == "thumb" else i["x"] < 0.45)]
    return max(ok, key=lambda i: i["c"] * len(i["t"]))["t"].strip() if ok else None


def crop_expr(box):
    x, y, w, h = box
    return f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}"


def sample_window(video, start, length):
    """[(seconds, name or None, layout or None)] for one window of the video."""
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    WORK_DIR.mkdir(parents=True)
    graph = (f"[0:v]fps=1/{STEP},split[a][b];"
             f"[a]{crop_expr(THUMB)},scale=960:-1[tr];[b]{crop_expr(LABEL)},scale=1680:-1[bl]")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-t", str(length),
                    "-i", str(video), "-filter_complex", graph,
                    "-map", "[tr]", str(WORK_DIR / "tr_%05d.png"), "-map", "[bl]", str(WORK_DIR / "bl_%05d.png")],
                   check=True)
    trs, bls = sorted(WORK_DIR.glob("tr_*.png")), sorted(WORK_DIR.glob("bl_*.png"))
    text = {**ocr(trs), **ocr(bls)}
    samples = []
    for k in range(len(trs)):
        n = f"{k + 1:05d}.png"
        thumb, label_ = read_name(text.get(f"tr_{n}", []), "thumb"), read_name(text.get(f"bl_{n}", []), "label")
        samples.append([start + k * STEP, thumb or label_, "share" if thumb else ("speaker" if label_ else None)])
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    return samples


def run(stage3, session, max_minutes):
    video = find_speaker_view_video(stage3, session)
    if not video:
        print("  no speaker-view video found locally -- skipped")
        return None
    total = min(session["duration_seconds"], max_minutes * 60) if max_minutes else session["duration_seconds"]
    print(f"  reading names from {video.name} ({total / 60:.0f} min)", flush=True)
    samples = []
    for start in range(0, int(total), WINDOW):
        samples += sample_window(video, start, min(WINDOW, total - start))
        print(f"    {min(start + WINDOW, total) / 60:.0f}/{total / 60:.0f} min", flush=True)
    named = sum(1 for s in samples if s[1])
    share = named / len(samples) if samples else 0
    result = {
        "video_id": session["video_id"], "video": str(video), "step": STEP,
        "partial": bool(max_minutes and max_minutes * 60 < session["duration_seconds"]),
        "label_share": round(share, 3), "usable": share >= MIN_LABEL_SHARE,
        "names": dict(Counter(s[1] for s in samples if s[1]).most_common()),
        "samples": samples,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{slug(session)}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False))
    return out_path, result


def main():
    flags, args = [], []
    it = iter(sys.argv[1:])
    max_minutes = None
    for a in it:
        if a == "--max-minutes":
            max_minutes = float(next(it))
        elif a.startswith("--"):
            flags.append(a)
        else:
            args.append(a)
    if not args:
        print("Usage: .venv/bin/python scripts/03c-nametag.py [--force] [--max-minutes N] <slug or salon number> [...]", file=sys.stderr)
        sys.exit(1)
    sessions = [s for s in json.load(open(SESSIONS_PATH)) if selected(s, args)]
    if not sessions:
        print(f"nothing in {SESSIONS_PATH} matches {args}", file=sys.stderr)
        sys.exit(1)
    ensure_ocr_tool()
    stage3 = _load_stage3()
    failures = 0
    for session in sessions:
        print(f"{label(session)}:", flush=True)
        if (OUT_DIR / f"{slug(session)}.json").exists() and "--force" not in flags:
            print("  already read (use --force to redo)")
            continue
        started = time.time()
        try:
            res = run(stage3, session, max_minutes)
        except Exception as e:  # keep a long batch going past one bad recording
            failures += 1
            print(f"  FAILED: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            continue
        if res:
            _, r = res
            top = ", ".join(f"{n} ({c})" for n, c in list(r["names"].items())[:5])
            print(f"  {'usable' if r['usable'] else 'NOT USABLE (labels in only %d%% of frames)' % round(100 * r['label_share'])}: "
                  f"{top} · {(time.time() - started) / 60:.1f} min", flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
