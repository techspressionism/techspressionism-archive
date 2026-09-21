#!/usr/bin/env python3
"""Draft a YouTube description with chapters for every recording -> private/youtube-descriptions/<slug>.txt

    python3 scripts/make-youtube-descriptions.py            # all recordings
    python3 scripts/make-youtube-descriptions.py --slug salon-081

For a salon or roundtable the chapters are the moments each identified speaker first speaks (YouTube needs a first chapter at 0:00, at least
three chapters, and at least 10 seconds between them). Interviews and presentations get no chapters (one or two voices). Every description ends
with the link to the searchable transcript in the archive. NOTHING is uploaded or changed on YouTube: these are drafts to read and paste.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from lib_media import label, slug            # noqa: E402
from lib_speakers import is_not_speaker      # noqa: E402
import importlib.util                        # noqa: E402
_spec = importlib.util.spec_from_file_location("site06", ROOT / "scripts" / "06-build-site.py")
_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_site)              # for page_name(): the recording's descriptive address

OUT = ROOT / "private" / "youtube-descriptions"
BASE = "https://techspressionism.com/archive"


def stamp(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def chapters(entry):
    if entry.get("type") not in ("salon", "roundtable"):
        return []
    first, total = {}, {}
    for seg in entry["segments"]:
        sp, a, b = seg.get("speaker"), seg.get("start"), seg.get("end")
        if not sp or is_not_speaker(sp) or a is None:
            continue
        first.setdefault(sp, float(a))
        total[sp] = total.get(sp, 0) + max(0.0, float(b or a) - float(a))
    rows = sorted(((t, sp) for sp, t in first.items() if total.get(sp, 0) >= 60), key=lambda r: r[0])
    out, last = [], -99
    for t, sp in rows:
        if t - last >= 15:
            out.append((t, sp))
            last = t
    if not out or out[0][0] > 5:
        out.insert(0, (0.0, "Introduction"))
    else:
        out[0] = (0.0, out[0][1] if out[0][1] != "Colin Goldberg" else "Introduction")
    return out if len(out) >= 3 else []


def description(entry):
    sl = slug(entry)
    lines = [f"{label(entry)}: {entry.get('session_title') or 'Untitled'}"]
    if entry.get("date_recorded"):
        lines.append(f"Recorded {entry['date_recorded']}.")
    if entry.get("interviewee"):
        lines.append(f"{entry['interviewee']}, interviewed by {entry['interviewer']}." if entry.get("interviewer") else entry["interviewee"])
    elif entry.get("moderator"):
        lines.append(f"Moderated by {entry['moderator']}.")
    ch = chapters(entry)
    if ch:
        lines += ["", "Chapters"] + [f"{stamp(t)} {name}" for t, name in ch]
    lines += ["", f"Read the searchable, timestamped transcript: {BASE}/{_site.page_name(entry)}/",
              "Techspressionism is an artistic approach in which technology is utilized as a means to express emotional experience.",
              "https://techspressionism.com/"]
    return "\n".join(lines) + "\n"


def main():
    only = sys.argv[sys.argv.index("--slug") + 1] if "--slug" in sys.argv else None
    OUT.mkdir(parents=True, exist_ok=True)
    n = withch = 0
    for entry in json.loads((ROOT / "corpus" / "corpus.json").read_text()):
        sl = slug(entry)
        if only and sl != only:
            continue
        text = description(entry)
        (OUT / f"{sl}_{entry['video_id']}.txt").write_text(text, encoding="utf-8")
        n += 1
        withch += "\nChapters\n" in text
    print(f"{n} description drafts ({withch} with chapters) -> {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
