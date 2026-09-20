#!/usr/bin/env python3
"""Move a Whisper transcript onto the YouTube video's timeline when its audio starts earlier or later than the video.

Whisper transcribes the audio it is given (often the original Zoom recording). If the YouTube video was trimmed or had
something added at the start, every Whisper time is off by a constant. check-whisper-alignment.py reports such a
recording as NOT ALIGNED. This tool finds the constant by sliding the Whisper words against YouTube's own captions
(the same word-pair test as the checker), and if the best shift makes the recording clearly ALIGNED it applies it:

    python3 scripts/shift-whisper-timeline.py roundtable-005            # find and apply
    python3 scripts/shift-whisper-timeline.py roundtable-005 --dry-run  # only report

Words that fall before 0:00 of the YouTube video (audio that was cut) are dropped. The shift is recorded in the file
("time_offset_seconds") and the original is kept once as raw/whisper/<slug>.unshifted.json. Run Stages 2, 4, 5, 6
afterwards. Nothing is guessed: if no shift gives a good match, nothing changes.
"""
import glob
import importlib.util
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("chk", HERE / "check-whisper-alignment.py")
chk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chk)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    slug, dry = args[0], "--dry-run" in sys.argv
    stage2 = chk._stage2()
    vtts = sorted(glob.glob(str(ROOT / "raw" / "captions" / slug / "*.vtt")))
    path = ROOT / "raw" / "whisper" / f"{slug}.json"
    if not vtts or not path.exists():
        sys.exit(f"{slug}: needs raw/captions/{slug}/*.vtt and raw/whisper/{slug}.json")
    _, _, youtube = stage2.parse_youtube_captions(Path(vtts[0]).read_text(errors="replace"))
    data = json.loads(path.read_text())
    words = [(w["start"], w["text"]) for w in data["words"]]
    duration = max(t for t, _ in words)

    def score(shift):
        vals = []
        for i in range(1, chk.WINDOWS + 1):
            a = duration * i / (chk.WINDOWS + 1)
            w = chk._pairs(chk._window([(t + shift, x) for t, x in words], a, a + chk.WINDOW_SECONDS))
            y = chk._pairs(chk._window(youtube, a, a + chk.WINDOW_SECONDS))
            if len(w) > 10 and len(y) > 10:
                vals.append(len(w & y) / min(len(w), len(y)))
        return sum(vals) / len(vals) if vals else 0.0

    coarse = max((score(s), s) for s in range(-300, 301, 2))
    fine = max((score(s / 4), s / 4) for s in range(int(coarse[1] * 4) - 12, int(coarse[1] * 4) + 13))
    s_best, shift = fine
    base = score(0)
    print(f"{slug}: no shift scores {base:.2f}; best shift {shift:+.2f} s scores {s_best:.2f}")
    if s_best < 0.6 or s_best < base + 0.2:
        print("  no shift gives a clear match: nothing changed")
        return
    if dry:
        print("  dry run: nothing changed")
        return
    orig = path.with_suffix(".unshifted.json")
    if not orig.exists():
        shutil.copy2(path, orig)
    src = json.loads(orig.read_text())
    keep = []
    for w in src["words"]:
        t = w["start"] + shift
        if t >= 0:
            keep.append({**w, "start": round(t, 2), **({"end": round(w["end"] + shift, 2)} if "end" in w else {})})
    dropped = len(src["words"]) - len(keep)
    src["words"] = keep
    if src.get("cues"):
        cues = []
        for c in src["cues"]:
            if c.get("start", 0) + shift >= 0:
                cues.append({**c, **{k: round(c[k] + shift, 2) for k in ("start", "end") if k in c}})
        src["cues"] = cues
    src["time_offset_seconds"] = shift
    src["words_before_video_start_dropped"] = dropped
    path.write_text(json.dumps(src, ensure_ascii=False))
    print(f"  applied {shift:+.2f} s; dropped {dropped} words from before the video starts. Re-check with check-whisper-alignment.py")


if __name__ == "__main__":
    main()
