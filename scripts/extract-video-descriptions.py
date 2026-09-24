#!/usr/bin/env python3
"""Stage 1c -- the narrative part of each recording's YouTube description, for the "Video Description" section on its page.

Reads the raw description from raw/video_json/<id>.json (harvest) or raw/video_desc_fetch/<id>.json (fetch-video-descriptions.py) and
keeps only narrative text: drops the title/moderator header block, every line with a time code (the presenter lists and chapters),
list headings (ARTIST PRESENTATIONS, FEATURED ARTISTS ...), dashed rules, lines that are only a web address, lines that are only a person's
name, the standing salon boilerplate, and hashtags. Written to data/video-descriptions.json ({video id: text}; paragraphs separated by a
blank line; videos with no narrative are left out). raw/ is not in git and CI cannot see it, so the result is tracked in data/.

    python3 scripts/extract-video-descriptions.py [--show salon-092]
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMECODE = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)")
URL_ONLY = re.compile(r"^\(?<?(https?://|www\.)\S+>?\)?[.,;]?$", re.I)
HEADING = re.compile(r"^(artist presentations?|featured artists?|artists?|presenters?|participants?|speakers?|artist screen ?shares?:?|screen ?shares?:?|schedule|agenda|"
                     r"presentations?|panelists?|guests?|moderator|moderators?|curator|curators?|hosts?|line ?up|chapters?|timestamps?)\s*:?$", re.I)
ROLE_LINE = re.compile(r"^(moderat(or|ed by)|curated by|hosted by|interview(er|ed by)|co-?moderat\w*)\b.*", re.I)
RULE = re.compile(r"^[-–—_=*~\s]{4,}$")
BOILERPLATE = re.compile(r"(these meetups are conducted|an archive of past techspressionist salons|the first techspressionist salon was held|"
                         r"salons are (free|open)|to (register|rsvp)|subscribe to|follow us on|zoom link|join us (on|via|at) )", re.I)
HEADER_WORDS = re.compile(r"(techspressionist|salon|interview|roundtable|presentation|//|moderat|curated|january|february|march|april|may |june|july|august|september|october|november|december)", re.I)
NAME_ONLY = re.compile(r"^[A-ZÀ-Ý][\w'’.\-]+(?: (?:[A-ZÀ-Ý][\w'’.\-]+|de|van|von|da|del|la|le|di|bin|el)){0,4}(?: \([^)]*\))?$")


def narrative(desc):
    paras = [p for p in re.split(r"\n\s*\n", desc.replace("\r", "").strip()) if p.strip()]
    if paras:
        first = [l.strip() for l in paras[0].split("\n") if l.strip()]
        if len(first) <= 4 and all(len(l) < 140 for l in first) and any(HEADER_WORDS.search(l) for l in first) and not any(l.endswith((".", "!", "?")) and len(l) > 60 for l in first):
            paras = paras[1:]          # the title / date / moderator block
    out = []
    for p in paras:
        keep = []
        for raw in p.split("\n"):
            line = raw.strip()
            if not line or RULE.match(line) or URL_ONLY.match(line) or TIMECODE.search(line) or HEADING.match(line) or ROLE_LINE.match(line):
                continue
            if BOILERPLATE.search(line) or re.fullmatch(r"(#\w+\s*)+", line):
                continue
            if len(line) < 70 and NAME_ONLY.match(line) and not line.endswith((".", ",")) and len(line.split()) <= 5 and not any(w in line for w in ("&", " and ")):
                continue               # a person's name on a line by itself: a presenter list or a heading above a bio that repeats the name
            keep.append(line)
        text = " ".join(keep).strip()
        if len(re.sub(r"\W", "", text)) >= 25:
            out.append(text)
    return "\n\n".join(out)


def raw_description(vid):
    for d in ("video_json", "video_desc_fetch"):
        f = ROOT / "raw" / d / f"{vid}.json"
        if f.exists():
            return json.load(open(f)).get("description") or ""
    return None


def main():
    sessions = json.load(open(ROOT / "data" / "sessions.json"))
    show = sys.argv[sys.argv.index("--show") + 1] if "--show" in sys.argv else None
    out, missing = {}, []
    for s in sessions:
        d = raw_description(s["video_id"])
        if d is None:
            missing.append(s["video_id"])
            continue
        text = narrative(d)
        sl = f"{s['type']}-{int(s['number']):03d}"
        if show and sl == show:
            print("--- RAW ---\n" + d[:3000] + "\n--- NARRATIVE ---\n" + text)
        if text:
            out[s["video_id"]] = text
    if not show:
        (ROOT / "data" / "video-descriptions.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
        print(f"{len(out)} recordings have narrative description text; {len(sessions) - len(out) - len(missing)} have none; {len(missing)} raw descriptions not fetched yet")


if __name__ == "__main__":
    main()
