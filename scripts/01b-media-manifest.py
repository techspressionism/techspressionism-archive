#!/usr/bin/env python3
"""Stage 1b -- classify the non-Salon recordings on the YouTube channel
(interviews, roundtables, presentations) and download their metadata.

Reads raw/channel.json (the channel harvest), writes:
  data/media-manifest.json   one entry per recording: type, number, title, ...
  raw/media_json/<id>.json   full yt-dlp metadata (description, upload date)

The manifest is the hand-checkable source of truth for this stage; edit it
(or MANUAL below) rather than the generated sessions. Stage 1c turns it into
data/sessions.json entries.

Usage:
    python3 scripts/01b-media-manifest.py            # classify + fetch missing metadata
    python3 scripts/01b-media-manifest.py --no-fetch # classify only
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_VIDEO = Path.home() / "Documents" / "~TECHSPRESSIONISM" / "VIDEO"
CHANNEL_PATH = ROOT / "raw" / "channel.json"
MEDIA_JSON_DIR = ROOT / "raw" / "media_json"
MANIFEST_PATH = ROOT / "data" / "media-manifest.json"

# Hello Uzbekistan presentation series (2026, American Corner Makerspace,
# Qarshi), in the order techspressionism.com lists them -- that order is the
# presentation number.
PRESENTATION_IDS = [
    "z8fZvhQgl80",  # 1 Digital Painting and Drawing 1
    "GvAkzINQBT8",  # 2 AI as Creative Collaborator
    "DtGglnJ4mdI",  # 3 Collaborations Between Artists and Scientists
    "9u3WpSKe3bM",  # 4 Digital Painting & Drawing 2
    "XgXtLT1I-T4",  # 5 Moving Images: Sequencing as Visual Expression
    "V3NO6tMIiPU",  # 6 Digital Photography
    "lW17XAepKs4",  # 7 How Technology Changes Artist Interactions with Cultural Sources
    "1OgIwvY5_F4",  # 8 Digital Sculpture and World Creation
]
PROVISIONAL = set()

# Unnumbered interviews: number is the gap in the #1-#28 series (16, 17) or
# the next after the last (Claudia Hart). Order within a gap follows upload date.
MANUAL_INTERVIEW_NUMBERS = {}

INTERVIEW_RE = re.compile(
    r"^(?P<who>.+?)\s+interviewed\s+by\s+(?P<by>.+?)(?:\s*[-:(]|\s+Techspressionist\b|$)", re.I)
NUMBER_RE = re.compile(r"(?:Interview(?:\s+Series)?|Roundtable)\s*#?\s*(\d+)", re.I)


# Local Zoom-export folders (VIDEO/INTERVIEWS/<NAME>) -> interviewee. Only
# folders that could be matched with confidence are listed.
INTERVIEW_FOLDERS = {
    "ANDREA": "Andrea Bonaceto", "ANNE": "Anne Spalter", "BRANDON": "Brandon Gellis",
    "CARI ANN": "cari ann shim sham*", "CARTER": "Carter Hodgkin", "CEE": "Cee Moses",
    "CLAUDIA": "Claudia Hart", "COLIN": "Colin Goldberg", "DARCY": "Darcy Gerbarg",
    "DAVO": "Davonte Bradley", "FRANK": "Frank Gillette", "JOSEPH": "Joseph Nechvatal",
    "KAREN": "Karen LaFleur", "MALAVIKA": "Malavika Mandal Andrew", "MICHAEL": "Michael Rees",
    "NINA": "Nina Sobell", "PATRICK": "Patrick Lichty", "PAUL": "Paul D. Milller",
    "RANDI": "Randi Matushevitz", "RENATA": "Renata Janiszewska", "ROZ": "Roz Dimon",
    "SASHA": "Sasha Stiles", "STEVE": "Steve Miller", "SUZANNE": "Suzanne Anker",
    "TOMMY": "Tommy Mintz", "VERNEDA": "Verneda Lights", "VICTOR": "Victor Acevedo",
}
ZOOM_STAMP_RE = re.compile(r"GMT(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})")
VTT_END_RE = re.compile(r"-->\s*(\d{2}):(\d{2}):(\d{2})")


def zoom_local_datetime(name):
    """GMT stamp in a Zoom export filename -> New York calendar date."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    m = ZOOM_STAMP_RE.search(name)
    if not m:
        return None
    dt = datetime(*map(int, m.groups()), tzinfo=timezone.utc).astimezone(ZoneInfo("America/New_York"))
    return dt.strftime("%Y-%m-%d")


def vtt_last_end(path):
    last = 0
    for m in VTT_END_RE.finditer(path.read_text(errors="replace")):
        h, mi, sec = map(int, m.groups())
        last = h * 3600 + mi * 60 + sec
    return last


def find_local(item, duration):
    """Local Zoom folder for this recording: its speaker-labelled transcript
    (chosen by matching the video's duration when there are several) and
    the recording date from the Zoom filename."""
    folder = None
    if item["type"] == "roundtable":
        folder = LOCAL_VIDEO / "ROUNDTABLE" / f"ROUNDTABLE_{item['number']}"
    elif item["type"] == "interview":
        for f, who in INTERVIEW_FOLDERS.items():
            if item.get("interviewee") and item["interviewee"].startswith(who):
                folder = LOCAL_VIDEO / "INTERVIEWS" / f
    if not folder or not folder.is_dir():
        return {}
    out = {"local_dir": str(folder)}
    vtts = sorted(folder.rglob("*.transcript.vtt"))
    best = None
    for v in vtts:
        gap = abs(vtt_last_end(v) - (duration or 0))
        if duration and gap <= 0.15 * duration and (best is None or gap < best[0]):
            best = (gap, v)
    if best:
        out["local_transcript"] = str(best[1])
        out["local_date"] = zoom_local_datetime(best[1].name)
    else:
        stamps = sorted(filter(None, (zoom_local_datetime(f.name) for f in folder.rglob("GMT*"))))
        if stamps:
            out["local_date"] = stamps[0]
    return out


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip(" -:—–")


def classify(entries):
    out = []
    for v in entries:
        title, vid = v["title"], v["id"]
        if vid in PRESENTATION_IDS:
            out.append({"video_id": vid, "type": "presentation", "title_raw": title,
                        "session_title": re.sub(r":?\s*Techspressionism at .*$", "", title).strip()})
        elif re.search(r"interviewed by", title, re.I):
            m = INTERVIEW_RE.match(title)
            n = re.search(r"Interview(?:\s+Series)?\s*#\s*(\d+)", title, re.I)
            out.append({"video_id": vid, "type": "interview", "title_raw": title,
                        "number": int(n.group(1)) if n else None,
                        "interviewee": clean(m.group("who")) if m else None,
                        "interviewer": clean(m.group("by")) if m else None})
        elif re.search(r"roundtable|curators in conversation", title, re.I):
            n = re.search(r"Roundtable\s*#?\s*0*(\d+)", title, re.I)
            num = int(n.group(1)) if n else 1  # "Curators in Conversation" is Roundtable 01
            topic = re.sub(r"^.*?Roundtable\s*#?\s*\d+\s*(//|-|:)?\s*", "", title, flags=re.I) if n else title
            if not n:
                topic = "Curators in Conversation"
            elif re.match(r"^(Four Techspressionist Artists|Founders: Five Years Later)", title):
                topic = re.sub(r"\s*//.*$", "", title)
            out.append({"video_id": vid, "type": "roundtable", "title_raw": title,
                        "number": num, "session_title": clean(topic)})
    return out


def assign_numbers(items, meta):
    """Presentations are numbered by upload order; unnumbered interviews take
    the gaps in the series, then continue past the highest."""
    def key(i):
        return (meta.get(i["video_id"], {}).get("upload_date") or "", i["video_id"])

    for i in items:
        if i["type"] == "presentation":
            i["number"] = PRESENTATION_IDS.index(i["video_id"]) + 1
    ivs = [i for i in items if i["type"] == "interview"]
    used = {i["number"] for i in ivs if i["number"]}
    free = [n for n in range(1, max(used) + 1) if n not in used]
    nxt = max(used) + 1
    for i in sorted([i for i in ivs if not i["number"]], key=key):
        if free:
            i["number"] = free.pop(0)
        else:
            i["number"], nxt = nxt, nxt + 1


def fetch_meta(video_ids):
    MEDIA_JSON_DIR.mkdir(parents=True, exist_ok=True)
    for vid in video_ids:
        out = MEDIA_JSON_DIR / f"{vid}.json"
        if out.exists():
            continue
        r = subprocess.run(["yt-dlp", "--dump-single-json", "--skip-download", "--no-warnings",
                            f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  FAILED {vid}: {r.stderr.strip()[:120]}")
            continue
        out.write_text(r.stdout)
        print(f"  fetched {vid}")


def main():
    entries = json.loads(CHANNEL_PATH.read_text())["entries"]
    items = classify(entries)
    if "--no-fetch" not in sys.argv:
        fetch_meta([i["video_id"] for i in items])
    meta = {}
    for i in items:
        p = MEDIA_JSON_DIR / f"{i['video_id']}.json"
        if p.exists():
            meta[i["video_id"]] = json.loads(p.read_text())
    assign_numbers(items, meta)
    for i in items:
        i["provisional"] = i["video_id"] in PROVISIONAL or None
        i.update(find_local(i, (meta.get(i["video_id"]) or {}).get("duration")))
    items.sort(key=lambda i: (i["type"], i["number"]))
    MANIFEST_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False))
    by = {}
    for i in items:
        by[i["type"]] = by.get(i["type"], 0) + 1
    print(f"Wrote {len(items)} entries to {MANIFEST_PATH}: {by}")


if __name__ == "__main__":
    main()
