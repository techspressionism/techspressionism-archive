#!/usr/bin/env python3
"""Stage 1 — metadata harvest. Parses raw/video_json/*.json into data/sessions.json.

Usage:
    python3 scripts/01-harvest.py raw/video_json/*.json
    python3 scripts/01-harvest.py            # defaults to all files in raw/video_json/
"""
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO_JSON_DIR = ROOT / "raw" / "video_json"
SITE_TEXT_DIR = ROOT / "raw" / "site_text"
THUMBNAIL_DIR = ROOT / "raw" / "thumbnails"
OVERRIDES_PATH = ROOT / "data" / "manual-overrides.json"
OUT_PATH = ROOT / "data" / "sessions.json"

MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
FULL_DATE_RE = re.compile(rf"\b({MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.IGNORECASE)
NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")

SALON_NUMBER_RE = re.compile(r"Salon\s*#?\s*(\d+)", re.IGNORECASE)
MODERATOR_RE = re.compile(r"(?im)^\s*moderat(?:ors?|ed by)\b\s*:?\s*([^\n]+)")

TIME_RE = r"(\d{1,2}:\d{2}(?::\d{2})?)"
# timestamp first: "00:01:06 - Name – Country" or "00:01:06 - Name (Country)"
TS_FIRST_RE = re.compile(rf"^{TIME_RE}\s*[-–—]?\s*(.+)$")
# name first: "Victor Acevedo - 0:06:12"
NAME_FIRST_RE = re.compile(rf"^(.+?)\s*[-–—]\s*{TIME_RE}\s*$")
PAREN_COUNTRY_RE = re.compile(r"^(.+?)\s*\(([^()]+)\)\s*$")

NBSP = "\xa0"


def clean(s):
    return s.replace(NBSP, " ").strip(" \t.-–—:") if s else s


def parse_timestamp_to_seconds(ts):
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = 0, parts[0], parts[1]
    else:
        return None
    return h * 3600 + m * 60 + s


def seconds_to_display(total):
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


BOILERPLATE_DATE_RE = re.compile(r"first\s+techspressionist\s+salon\s+was\s+held\s+on", re.IGNORECASE)


def _boilerplate_context(text, pos):
    """True if a date match at `pos` sits in the recurring "The First
    Techspressionist Salon was held on September 1, 2020" boilerplate that
    appears in most descriptions and every site page -- not a session date."""
    return bool(BOILERPLATE_DATE_RE.search(text[max(0, pos - 60):pos]))


def find_recording_date(text):
    """Return (date_iso, flag) using the first real month-name or numeric
    date found, skipping the first-salon boilerplate date."""
    for m in FULL_DATE_RE.finditer(text):
        if _boilerplate_context(text, m.start()):
            continue
        month_name, day, year = m.groups()
        try:
            return datetime.strptime(f"{month_name.capitalize()} {day} {year}", "%B %d %Y").strftime("%Y-%m-%d"), None
        except ValueError:
            pass
    for m in NUMERIC_DATE_RE.finditer(text):
        if _boilerplate_context(text, m.start()):
            continue
        mo, day, year = m.groups()
        year = int(year)
        if year < 100:
            year += 2000
        try:
            return datetime(year, int(mo), int(day)).strftime("%Y-%m-%d"), None
        except ValueError:
            pass
    return None, "recording_date_unparseable"


def parse_session_number_and_title(title_raw):
    """Extract session number and a session title/topic from the raw YouTube title."""
    m = SALON_NUMBER_RE.search(title_raw)
    number = int(m.group(1)) if m else None
    flags = []
    if number is None:
        flags.append("session_number_unparseable")
        return None, None, flags

    remainder = title_raw[m.end():]
    # Common separators observed: "//" and " - "
    if "//" in remainder:
        parts = [p.strip(" -–—") for p in remainder.split("//", 1)]
        session_title = parts[1].strip() if len(parts) > 1 else None
    else:
        # split on " - " style separators; titles are inconsistent about
        # spacing/doubling around the dash ("Tell- March 30, 2022", "Techspressionism -- October 3, 2024"),
        # so match 1-2 dashes with whitespace on at least one side, not just " - "
        segments = [s.strip() for s in re.split(r"\s*-{1,2}\s+", remainder) if s.strip()]

        def is_pure_date_segment(s):
            return bool(FULL_DATE_RE.fullmatch(s) or NUMERIC_DATE_RE.fullmatch(s))

        # drop segments that are *entirely* a date or a "Moderated by X" clause,
        # wherever they fall (not always last -- e.g. "#43 - FEMME TECH - May
        # 11, 2022 - Moderated by Roz Dimon" has the date in the middle)
        segments = [
            s for s in segments
            if not is_pure_date_segment(s) and not re.match(r"(?i)^moderat(?:or|ed by)\b", s)
        ]

        # a date can also be glued onto the end of a segment with no separator
        # at all ("Miller, Henderson, Woods 2/16/21") -- trim just that tail
        # rather than losing the whole segment to a whole-string date check
        if segments:
            for date_re in (FULL_DATE_RE, NUMERIC_DATE_RE):
                m = date_re.search(segments[-1])
                if m and m.end() >= len(segments[-1]) - 2:
                    segments[-1] = segments[-1][: m.start()].strip(" ,-–—")
                    break
            segments = [s for s in segments if s]

        session_title = " - ".join(segments) if segments else None

    if not session_title:
        # some titles put the real title *before* "Salon #N" instead of after
        # ("Techspressionism 2021 Opening @ Techspressionist Salon #29",
        # "Live from Chelsea // Techspressionist Salon 95") -- try that span
        # before giving up
        before = title_raw[: m.start()].strip(" -–—@/")
        before = re.sub(r"(?i)\bTechspressionist(?:ic|s)?\b\s*$", "", before).strip(" -–—@/")
        session_title = before or None

    if not session_title:
        flags.append("session_title_unparseable")
        session_title = None

    return number, session_title, flags


def parse_speaker_line(line):
    """Try both timestamp-first and name-first orderings. Returns dict or None."""
    line = line.strip()
    if not line:
        return None

    m = TS_FIRST_RE.match(line)
    if m:
        ts, rest = m.groups()
        seconds = parse_timestamp_to_seconds(ts)
        rest = clean(rest)
        # a handful of sessions prefix each entry with a bare "//" bullet
        # ("00:14:34 // Allen Hirsh -- Chevy Chase, MD USA // url") -- strip
        # it before any separator detection below, or it gets caught up in
        # whichever separator (en-dash, "//") the line also uses
        rest = re.sub(r"^/+\s*", "", rest)
        pm = PAREN_COUNTRY_RE.match(rest)
        if pm:
            name, country = clean(pm.group(1)), clean(pm.group(2))
        elif "–" in rest:
            segs = [clean(s) for s in rest.split("–")]
            country = segs[-1].split("//")[0].strip()  # drop a trailing "// url" if present
            name = " – ".join(segs[:-1])
        elif "//" in rest:
            # "Name // Location" (most sessions) or "Name, Location // url"
            # (a few, e.g. Salon 97, once the leading bullet above is gone)
            segs = [clean(s) for s in rest.split("//")]
            head = segs[0]
            if "," in head:
                name, _, country = head.partition(",")
                name, country = clean(name), clean(country) or None
            else:
                name = head
                country = segs[1] if len(segs) > 1 and segs[1] else None
        else:
            name, country = rest, None
        if name:
            return {"name": name, "country": country, "start_seconds": seconds}

    m = NAME_FIRST_RE.match(line)
    if m:
        name, ts = m.groups()
        name = clean(name)
        seconds = parse_timestamp_to_seconds(ts)
        if name:
            return {"name": name, "country": None, "start_seconds": seconds}

    return None


def parse_speaker_index(description):
    speakers = []
    unmatched_timestamp_lines = 0
    for line in description.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        has_timestamp = bool(re.search(TIME_RE, stripped))
        entry = parse_speaker_line(stripped)
        if entry and is_plausible_speaker(entry):
            entry["country"] = sanitize_country(entry.get("country"))
            speakers.append(entry)
        elif has_timestamp and re.match(r"^\d{1,2}:\d{2}", stripped):
            # looks like a timestamp line but didn't parse cleanly
            unmatched_timestamp_lines += 1

    flags = []
    if not speakers:
        flags.append("speaker_index_missing")
    elif unmatched_timestamp_lines:
        flags.append("speaker_index_partially_unparseable")
    return speakers, flags


def normalize_name(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


BARE_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
AGENDA_WORDS_RE = re.compile(
    r"\b(q\s*&\s*a|presentation|walkthrough|preview|exhibition|tour|panel|"
    r"discussion|screenshares?|artworks?|closing remarks|opening remarks|"
    r"video|intro(?:duction)?|break|lineup|schedule)\b",
    re.IGNORECASE,
)


def clean_speaker_name(name):
    """Strip a trailing " - Location" and quoted nicknames that leak into the
    name field from inconsistent description formatting ("Cynthia DiDonato -
    RI USA", 'Davonte "Davo" Bradley')."""
    if not name:
        return name
    name = re.sub(r'\s*["“”][^"“”]*["“”]\s*', " ", name)
    name = re.sub(r"\s*:.*$", "", name)  # "Tommy Mintz: Chief Curator" -> "Tommy Mintz"
    # " - Location" tail -- require spaces around the dash so real hyphenated
    # surnames ("Baron-Robbins") are untouched
    name = re.sub(r"\s+[-–—]\s*\S.*$", "", name)
    return re.sub(r"\s{2,}", " ", name).strip(" -–—,")


def is_plausible_speaker(entry):
    entry["name"] = clean_speaker_name(entry.get("name"))
    name = (entry.get("name") or "").strip()
    if len(name) < 3 or not any(c.isalpha() for c in name):
        return False
    if BARE_TIME_RE.match(name) or name.isdigit():
        return False
    if "http" in name.lower() or "//" in name:
        return False
    if AGENDA_WORDS_RE.search(name):
        return False
    if name.isupper() and len(name.split()) > 2:  # "SONIC FLIGHT: TOWARDS PEACE"
        return False
    return True


def sanitize_country(country):
    if not country:
        return None
    c = country.strip()
    if "http" in c.lower() or "//" in c or ":" in c or len(c) > 45:
        return None
    if any(ch.isdigit() for ch in c):  # real place names don't carry digits
        return None
    if AGENDA_WORDS_RE.search(c):
        return None
    return c or None


def parse_site_speaker_index(text):
    """techspressionism.com salon pages format speaker blocks several ways:
    - single-line, same as YouTube: 'HH:MM:SS – Name – Country'
    - two-line (older era): 'H:MM:SS – Name' followed by a bare 'Location' line
    - timestamp alone on its line, then 'Name // Location' on the next
    Join a bare-timestamp line onto the following line before parsing, and
    peek at the next non-blank line when no country was captured inline.
    """
    raw_lines = text.split("\n")
    lines = []
    i = 0
    while i < len(raw_lines):
        stripped = raw_lines[i].strip()
        if BARE_TIME_RE.match(stripped):
            j = i + 1
            while j < len(raw_lines) and not raw_lines[j].strip():
                j += 1
            if j < len(raw_lines):
                lines.append(f"{stripped} {raw_lines[j].strip()}")
                i = j + 1
                continue
        lines.append(raw_lines[i])
        i += 1

    speakers = []
    n = len(lines)
    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        entry = parse_speaker_line(line)
        if not entry:
            continue
        if entry["country"] is None:
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n:
                candidate = lines[j].strip()
                if (
                    candidate
                    and not re.search(TIME_RE, candidate)
                    and len(candidate) < 60
                    and not candidate.endswith(".")
                ):
                    entry["country"] = clean(candidate)
        if is_plausible_speaker(entry):
            entry["country"] = sanitize_country(entry.get("country"))
            speakers.append(entry)
    return speakers


def merge_site_speakers(yt_speakers, site_speakers, notes):
    site_by_name = {normalize_name(s["name"]): s for s in site_speakers}
    for sp in yt_speakers:
        if sp["country"] is None:
            match = site_by_name.get(normalize_name(sp["name"]))
            if match and match["country"]:
                sp["country"] = match["country"]
                notes.append(f"country_backfilled_from_site:{sp['name']}")
    # speakers present on the site but entirely absent from the YouTube description
    yt_names = {normalize_name(s["name"]) for s in yt_speakers}
    for s in site_speakers:
        if normalize_name(s["name"]) not in yt_names:
            yt_speakers.append(s)
            notes.append(f"speaker_added_from_site:{s['name']}")
    return yt_speakers


def parse_moderator(description):
    m = MODERATOR_RE.search(description)
    if not m:
        return None, ["moderator_missing"]
    name = clean(m.group(1))
    return (name or None), ([] if name else ["moderator_missing"])


def download_thumbnail(video_id, url):
    if not url:
        return
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    dest = THUMBNAIL_DIR / f"{video_id}.jpg"
    if dest.exists():
        return
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as e:
        print(f"  warning: could not download thumbnail for {video_id}: {e}", file=sys.stderr)


FLAG_FOR_FIELD = {
    "date_recorded": "recording_date_unparseable",
    "moderator": "moderator_missing",
    "session_title": "session_title_unparseable",
    "number": "session_number_unparseable",
    "speakers": "speaker_index_missing",
}


def load_overrides():
    if not OVERRIDES_PATH.exists():
        return {}
    with open(OVERRIDES_PATH) as f:
        return json.load(f)


def apply_overrides(session, overrides):
    override = overrides.get(session["video_id"])
    if not override:
        return
    source = override.get("source", "manual override")
    for key, value in override.items():
        if key == "source":
            continue
        session[key] = value
        clear_flag = FLAG_FOR_FIELD.get(key)
        if clear_flag and clear_flag in session["flags"]:
            session["flags"].remove(clear_flag)
        session["notes"].append(f"{key}_manually_overridden:{source}")


def harvest_one(video_json_path):
    with open(video_json_path) as f:
        d = json.load(f)

    title_raw = d.get("title", "")
    description = d.get("description", "") or ""
    upload_date_raw = d.get("upload_date")  # YYYYMMDD
    date_published = None
    if upload_date_raw:
        date_published = datetime.strptime(upload_date_raw, "%Y%m%d").strftime("%Y-%m-%d")

    number, session_title, title_flags = parse_session_number_and_title(title_raw)
    date_recorded, date_flag = find_recording_date(description) if description else (None, "recording_date_unparseable")
    moderator, mod_flags = parse_moderator(description)
    speakers, speaker_flags = parse_speaker_index(description)

    notes = []
    # the YouTube title usually carries the recording date too ("Salon 53 -
    # Photography and Media Confluence - September 28, 2022"); trust it when
    # the description had none
    if date_recorded is None and title_raw:
        title_date, _ = find_recording_date(title_raw)
        if title_date:
            date_recorded, date_flag = title_date, None
            notes.append("recording_date_from_title")
    site_path = SITE_TEXT_DIR / f"salon-{number}.txt" if number is not None else None
    if site_path and site_path.exists():
        site_text = site_path.read_text()
        site_speakers = parse_site_speaker_index(site_text)
        speakers = merge_site_speakers(speakers, site_speakers, notes)
        if not speakers and site_speakers:
            speaker_flags = [f for f in speaker_flags if f != "speaker_index_missing"]
        # a day-precision date on the site can resolve a description-only gap.
        # only trust a date in the page header -- deeper in, the recurring
        # boilerplate and artist bios ("began experimenting in 1983", etc.)
        # produce false matches.
        if date_recorded is None:
            site_date, site_date_flag = find_recording_date(site_text[:400])
            if site_date:
                date_recorded = site_date
                date_flag = None
                notes.append("recording_date_backfilled_from_site")

    flags = list(title_flags) + mod_flags + speaker_flags
    if date_flag:
        flags.append(date_flag)

    download_thumbnail(d.get("id"), d.get("thumbnail"))

    return {
        "video_id": d.get("id"),
        "url": f"https://www.youtube.com/watch?v={d.get('id')}",
        "type": "salon",
        "number": number,
        "title_raw": title_raw,
        "session_title": session_title,
        "date_recorded": date_recorded,
        "date_published": date_published,
        "duration_seconds": d.get("duration"),
        "thumbnail": d.get("thumbnail"),
        "moderator": moderator,
        "speakers": [
            {
                "name": s["name"],
                "country": s["country"],
                "start_seconds": s["start_seconds"],
                "start_display": seconds_to_display(s["start_seconds"]) if s["start_seconds"] is not None else None,
            }
            for s in speakers
        ],
        "transcript_source": None,
        "flags": flags,
        "notes": notes,
    }


def main():
    args = sys.argv[1:]
    if args:
        paths = [Path(p) for p in args]
    else:
        paths = sorted(VIDEO_JSON_DIR.glob("*.json"))

    if not paths:
        print(f"No video JSON files found in {VIDEO_JSON_DIR}", file=sys.stderr)
        sys.exit(1)

    overrides = load_overrides()

    sessions = []
    for p in paths:
        session = harvest_one(p)
        apply_overrides(session, overrides)
        sessions.append(session)
        flag_note = f" FLAGS: {session['flags']}" if session["flags"] else ""
        print(f"  parsed {p.name}: Salon {session['number']} — {session['session_title']!r}{flag_note}")

    sessions.sort(key=lambda s: (s["number"] is None, s["number"]))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(sessions, f, indent=2, ensure_ascii=False)

    print(f"\nWrote {len(sessions)} sessions to {OUT_PATH}")


if __name__ == "__main__":
    main()
