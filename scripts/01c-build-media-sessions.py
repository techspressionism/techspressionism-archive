#!/usr/bin/env python3
"""Stage 1c -- turn data/media-manifest.json (+ raw/media_json) into session
entries in data/sessions.json, next to the Salons, using the same schema.

Per type:
  interview     interviewee + interviewer come from the title; no speaker
                index exists, so both are listed without timestamps
  roundtable    participants/moderator from MANUAL (the descriptions are
                free-form bios); the date comes from the local Zoom export
  presentation  timestamped speaker index parsed from the description; the
                moderator introduces, opens the discussion, and closes

Recording date, in order of trust: local Zoom-export filename, date in the
title, date in the description, YouTube upload date (flagged). Every
candidate must fall 0-400 days before the upload date -- bio text and
boilerplate contain plenty of unrelated dates.

Usage:
    python3 scripts/01c-build-media-sessions.py
"""
import importlib.util
import json
import re
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "data" / "media-manifest.json"
MEDIA_JSON_DIR = ROOT / "raw" / "media_json"
SESSIONS_PATH = ROOT / "data" / "sessions.json"
THUMB_DIR = ROOT / "raw" / "thumbnails"
ASSET_THUMB_DIR = ROOT / "assets" / "thumbnails"

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_speakers import canonical_name  # noqa: E402

_spec = importlib.util.spec_from_file_location("harvest", Path(__file__).with_name("01-harvest.py"))
harvest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harvest)

# Participants and recording dates from the individual pages on
# techspressionism.com (checked Sept 2026). Roundtable/presentation
# descriptions on YouTube are free-form bios, so these are entered by hand.
MANUAL = {
    ("roundtable", 1): {"moderator": "Colin Goldberg", "speakers": [("Christiane Paul", "NYC, NY USA"), ("Helen A. Harrison", "Sag Harbor, NY USA")]},
    ("roundtable", 2): {"moderator": "Michael Pierre Price", "speakers": [("Roz Dimon", "Shelter Island, NY, USA"), ("Tommy Mintz", "NYC, NY, USA"), ("Renata Janiszewska", "Lion's Head, Ontario, Canada")]},
    ("roundtable", 3): {"moderator": "Michael Pierre Price", "speakers": [("Cynthia DiDonato", "North Providence, Rhode Island USA"), ("Karen LaFleur", "Cape Cod, MA USA"), ("Cynthia Beth Rubin", "New Haven, CT USA")]},
    ("roundtable", 4): {"moderator": "Michael Pierre Price", "speakers": [("Moritz Albrecht", "Frankfurt, Germany"), ("Steve Miller", "Sagaponack, NY USA")]},
    ("roundtable", 5): {"moderator": "Michael Pierre Price", "speakers": [("Colin Goldberg", "Bronx, NY"), ("Renata Janiszewska", "Lion's Head, Canada"), ("Karen LaFleur", "Cape Cod, MA"), ("Jan Swinburne", "Toronto, Canada"), ("ChatGPT", None)]},
    ("roundtable", 6): {"moderator": "Michael Pierre Price", "speakers": [("Colin Goldberg", None), ("Patrick Lichty", None), ("Steve Miller", None), ("Oz Van Rosen", None), ("Helen A. Harrison", None)]},
    ("presentation", 1): {"moderator": "Cynthia Beth Rubin"},
    ("presentation", 2): {"moderator": "cha :)"},
    ("presentation", 3): {"moderator": "Lee Day"},
    ("presentation", 4): {"moderator": "Gregory Little"},
    ("presentation", 5): {"moderator": "Lucy Boyd-Wilson"},
    # 6 and 8 have no speaker index anywhere, so their presenters were taken from the thumbnail graphic and
    # confirmed from the moderator's spoken introduction (Sept 2026). Start times are still unknown.
    ("presentation", 6): {"moderator": "Roz Dimon", "speakers": [
        ("Lee Day", "New York City USA"), ("Gregory Little", "Oberlin, Ohio USA"), ("Lee Musgrave", "White Salmon, Washington USA")]},
    ("presentation", 7): {"moderator": "Stephen Paré"},
    # 9: the Pollock-Krasner House & Study Center event (recorded live on Zoom 24 Feb 2021, produced by the Center).
    # Host and speakers are from the techspressionism.com page; check them against the recording. Its own series name.
    ("presentation", 9): {"moderator": "Helen A. Harrison", "series": "Pollock-Krasner House & Study Center", "title": "Technology and Art with Colin Goldberg",
        "speakers": [("Colin Goldberg", "North Bennington, VT USA"), ("Joyce Raimondo", "East Hampton, NY USA")]},
    ("presentation", 8): {"moderator": "Michael Pierre Price", "speakers": [
        ("Lucy Boyd-Wilson", "San Diego, California USA"), ("Annette Weintraub", "New York City USA"),
        ("Ramis Karimov", "Qarshi, Uzbekistan"), ("Jafar Rustamov", "Qarshi, Uzbekistan")]},
}

# Recording date per the site's own pages. Partial dates are kept partial.
SITE_DATES = {
    **{("interview", n): d for n, d in {
        1: "2021-01-22", 2: "2021-01-27", 3: "2021-02-05", 4: "2000", 5: "2021-02-12", 6: "2021-02-17",
        7: "2021-02-24", 8: "2021-03-01", 9: "2021-03-31", 10: "2021-03-31", 11: "2021-04-06",
        12: "2021-04-14", 13: "2021-04-14", 14: "2021-05-04", 15: "2021-06-01", 16: "2021-06-09",
        17: "2021-06-17", 18: "2021-06-23", 19: "2021-09-17", 20: "2021-10-13", 21: "2022-01-07",
        22: "2022-03-02", 23: "2022-02-28", 24: "2022-02-28", 25: "2023-02-03", 26: "2023-03",
        27: "2024-09-30", 28: "2025-05-06", 29: "2025-12-08", 30: "2026-09-21"}.items()},
    ("roundtable", 1): "2022-10-03", ("roundtable", 2): "2023-01-23", ("roundtable", 3): "2024-06-12",
    ("roundtable", 4): "2024-07-10", ("roundtable", 5): "2025-06-27", ("roundtable", 6): "2025-09-16",
    ("presentation", 1): "2026-02-12", ("presentation", 2): "2026-02-26",
    ("presentation", 9): "2021-02-24",
}
DATE_NOTES = {
    ("interview", 4): "original recording 2000, reposted Feb 4, 2021",
    ("interview", 13): "site gives the same date as interview #12 (April 14, 2021) -- possible copy/paste",
    ("interview", 15): "Colin Goldberg 2026-09-18: recorded June 1, 2021, as Roz Dimon says on the recording; the techspressionism.com page says June 2 -- correct it on the site",
    ("interview", 22): "YouTube title says March 1, 2022; site says March 2",
    ("roundtable", 2): "confirmed by Colin Goldberg 2026-09-18: recorded January 23, 2023 (the site page says 2022 -- typo)",
}
INTERVIEWEE_LOCATIONS = {
    1: "Rocky Point", 2: "Brooklyn, NY, USA", 3: "Shelter Island, NY, USA", 4: "Paris", 5: "Richmond, VA, USA",
    6: "Winona, MN, USA", 7: "Phoenix, AZ USA", 8: "Sagaponack, NY USA", 9: "London, UK", 10: "Laramie, WY",
    11: "North Bergen, NJ USA", 12: "Oberlin, OH USA", 13: "Augusta, GA USA", 14: "New York, NY", 15: "New York, NY",
    16: "NYC NY USA", 17: "NYC NY USA", 18: "Los Angeles CA USA", 19: "Mumbai", 20: "Toronto, Canada",
    21: "Los Angeles CA USA", 22: "Cape Cod MA USA", 23: "NYC // East Hampton NY USA", 24: "NYC // East Hampton NY USA",
    25: "Port Royal SC USA", 26: "NYC NY USA", 27: "NYC NY USA", 28: "NEAR NYC NY // USA", 29: "NYC NY // USA", 30: "Brooklyn, NY, USA",
}

TS_LINE = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–—]?\s*(.+?)\s*$")
DISCUSSION_ROLE = re.compile(r"(?i)^(discussion|questions|q\s*&\s*a)")
ANNOUNCE_ROLE = re.compile(r"(?i)^(general information|information on|closing)")


def iso(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def parse_presentation_index(description, moderator):
    entries = []
    for line in description.split("\n"):
        m = TS_LINE.match(line)
        if not m:
            continue
        seconds = harvest.parse_timestamp_to_seconds(m.group(1))
        label = re.sub(r"[.\s]+$", "", m.group(2))
        if label.lower() == "end":
            continue
        intro = re.match(r"(?i)^introduction(?:\s+by)?(?:\s+moderator)?\s*(.*)$", label)
        summary = re.match(r"(?i)^summary remarks,?\s*(.+)$", label)
        if intro:
            name = intro.group(1).strip() or moderator
        elif summary:
            name = summary.group(1).strip()
        elif DISCUSSION_ROLE.match(label):
            name = "Discussion"
        elif ANNOUNCE_ROLE.match(label):
            name = "Announcements"
        else:
            name = label
        if not name or (entries and entries[-1]["name"] == name):
            continue
        entries.append({"name": name, "country": None, "start_seconds": seconds,
                        "start_display": harvest.seconds_to_display(seconds)})
    return entries


def pick_date(item, meta, upload):
    def ok(d):
        try:
            gap = (upload - date.fromisoformat(d)).days
        except (TypeError, ValueError):
            return False
        return 0 <= gap <= 400

    key = (item["type"], item["number"])
    site, local = SITE_DATES.get(key), item.get("local_date")
    if site:
        # a Zoom export's own timestamp beats a hand-typed page date
        if local and len(site) == 10 and site != local and ok(local):
            return local, f"local_zoom_file (site page says {site})"
        return site, "techspressionism.com"
    title_date, _ = harvest.find_recording_date(meta["title"])
    desc_date, _ = harvest.find_recording_date(meta.get("description") or "")
    for d, source in ((local, "local_zoom_file"), (title_date, "title"), (desc_date, "description")):
        if d and ok(d):
            return d, source
    return upload.isoformat(), "upload_date"


def download_thumbnail(video_id):
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dest = THUMB_DIR / f"{video_id}.jpg"
    if not dest.exists():
        for name in ("maxresdefault", "hqdefault"):
            try:
                urllib.request.urlretrieve(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg", dest)
                break
            except Exception:
                dest.unlink(missing_ok=True)
    if dest.exists():
        (ASSET_THUMB_DIR / dest.name).write_bytes(dest.read_bytes())


def build(item):
    meta = json.loads((MEDIA_JSON_DIR / f"{item['video_id']}.json").read_text())
    upload = datetime.strptime(meta["upload_date"], "%Y%m%d").date()
    flags, notes = [], []
    recorded, source = pick_date(item, meta, upload)
    notes.append(f"date_recorded_from:{source}")
    if (item["type"], item["number"]) in DATE_NOTES:
        notes.append("date_note:" + DATE_NOTES[(item["type"], item["number"])])
    if source == "upload_date":
        flags.append("recording_date_estimated_from_upload")
    manual = MANUAL.get((item["type"], item["number"]), {})
    moderator = manual.get("moderator")
    speakers = []

    if item["type"] == "interview":
        for n, loc in ((item.get("interviewee"), INTERVIEWEE_LOCATIONS.get(item["number"])), (item.get("interviewer"), None)):
            if n:
                speakers.append({"name": n, "country": loc, "start_seconds": None, "start_display": None})
        title = item["interviewee"]
    else:
        title = manual.get("title") or item["session_title"]
        if item["type"] == "presentation":
            speakers = parse_presentation_index(meta.get("description") or "", moderator)
            people = {s["name"] for s in speakers} - {"Discussion", "Announcements"}
            if len(people) <= 1:
                # an "Introduction by X" line alone is no speaker index --
                # crediting the whole recording to X would be misattribution
                if manual.get("speakers"):
                    # who presented, entered by hand (no start times): from the video's thumbnail graphic,
                    # confirmed against the moderator's spoken introduction
                    speakers = [{"name": n, "country": loc, "start_seconds": None, "start_display": None}
                                for n, loc in manual["speakers"]]
                else:
                    speakers = [{"name": moderator, "country": None, "start_seconds": None, "start_display": None}] if moderator else []
        else:
            speakers = [{"name": n, "country": loc, "start_seconds": None, "start_display": None}
                        for n, loc in manual.get("speakers", [])]
    if item["type"] != "interview" and not any(s["start_seconds"] is not None for s in speakers):
        flags.append("speaker_index_missing")
    if not moderator and item["type"] != "interview":
        flags.append("moderator_missing")

    download_thumbnail(item["video_id"])
    for sp in speakers:
        sp["name"] = canonical_name(sp["name"])
    moderator = canonical_name(moderator) if moderator else None
    for k in ("interviewee", "interviewer"):
        if item.get(k):
            item[k] = canonical_name(item[k])
    if item["type"] == "interview":
        title = item["interviewee"]
    session = {
        "video_id": item["video_id"],
        "url": f"https://www.youtube.com/watch?v={item['video_id']}",
        "type": item["type"],
        "number": item["number"],
        "title_raw": item["title_raw"],
        "session_title": title,
        "date_recorded": recorded,
        "date_published": upload.isoformat(),
        "duration_seconds": meta.get("duration"),
        "thumbnail": f"https://i.ytimg.com/vi/{item['video_id']}/maxresdefault.jpg",
        "moderator": moderator,
        "speakers": speakers,
        "flags": flags,
        "notes": notes,
    }
    for k in ("interviewee", "interviewer", "local_transcript"):
        if item.get(k):
            session[k] = item[k]
    if manual.get("series"):                     # a presentation outside the Hello Uzbekistan series names its own series
        session["series"] = manual["series"]
    if item.get("provisional"):
        session["notes"].append("series_membership_unconfirmed")
    return session


def main():
    manifest = json.loads(MANIFEST_PATH.read_text())
    built = [build(i) for i in manifest]
    existing = json.loads(SESSIONS_PATH.read_text()) if SESSIONS_PATH.exists() else []
    keep = [s for s in existing if s.get("type", "salon") == "salon"]
    SESSIONS_PATH.write_text(json.dumps(keep + built, indent=2, ensure_ascii=False))
    for s in built:
        print(f"{s['type']:12} {s['number']:>3}  {s['date_recorded']:10}  {s['notes'][0].split(':',1)[1][:22]:22} "
              f"{len([x for x in s['speakers'] if x['start_seconds'] is not None])} timed / {len(s['speakers'])} speakers  {s['session_title'][:40]}")
    print(f"\nWrote {len(keep)} salons + {len(built)} other recordings to {SESSIONS_PATH}")


if __name__ == "__main__":
    main()
