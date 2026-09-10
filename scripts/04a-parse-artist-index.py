#!/usr/bin/env python3
"""Stage 4 prep -- parse the Techspressionist Visual Artist Index (the
Artists page, WordPress post ID 132) out of the site export into
data/artists.json: name, country, location, and known handles (site
profile slug, instagram, website, twitter) as alternate-spelling signal
for the Stage 4 name-correction pass.

Usage:
    python3 scripts/04a-parse-artist-index.py /path/to/export.xml
"""
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "artists.json"

NS = {
    "wp": "http://wordpress.org/export/1.2/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}

US_STATES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Loiusiana", "Maine", "Maryland", "Massachusetts",
    "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
    "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
}
STATE_TYPO_FIXES = {"Loiusiana": "Louisiana"}
STRONG_RE = re.compile(r"<strong>(.*?)</strong>", re.DOTALL)
ICON_LINK_RE = re.compile(r'<a href="([^"]+)"><img[^>]*alt="([^"]*)"')
TAG_RE = re.compile(r"<[^>]+>")


def clean(s):
    s = TAG_RE.sub("", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_artist_line(line):
    strong_match = STRONG_RE.search(line)
    if not strong_match:
        return None
    name = clean(strong_match.group(1))
    # many entries wrap the "Name - Location" separator inside the <strong>
    # ("<strong>Mario De Meyer<em> - </em></strong>Ghent"), leaving a dangling
    # dash/comma on the name after tag stripping
    name = re.sub(r"\s*[-–—,]+\s*$", "", name).strip()
    if not name:
        return None

    # location: whatever remains after the name's closing </strong> tag,
    # before the first icon link, minus a leading separator (- / em-dash)
    remainder = line[strong_match.end():]
    first_icon = ICON_LINK_RE.search(remainder)
    location_raw = remainder[: first_icon.start()] if first_icon else remainder
    location = clean(location_raw)
    location = re.sub(r"^[-–—/\s]+", "", location).strip()
    location = re.sub(r"[-–—/\s]+$", "", location).strip()

    links = {}
    for url, alt in ICON_LINK_RE.findall(line):
        key = alt.strip().lower() or "link"
        links.setdefault(key, []).append(url)

    profile_match = re.search(r'href="(https://techspressionism\.com/exhibition/[^"]+)"', line)

    return {
        "name": name,
        "location": location or None,
        "links": links,
        "profile_url": profile_match.group(1) if profile_match else None,
    }


def parse_artists_page(body_html):
    artists = []
    # Split on the literal <h3> delimiter rather than requiring a specific
    # header structure -- roughly half the headers are "<img flag/>Country"
    # (countries) and half are bare "<h3>StateName</h3>" (US states get
    # their own header, no flag image, nested under a near-empty "United
    # States" section). A regex anchored on the image tag silently
    # swallowed every state section into whatever country preceded it.
    chunks = body_html.split("<h3>")
    for chunk in chunks[1:]:
        header_end = chunk.find("</h3>")
        if header_end == -1:
            continue
        header_raw, block = chunk[:header_end], chunk[header_end + len("</h3>"):]
        header_name = clean(header_raw)
        if not header_name:
            continue

        if header_name in US_STATES:
            country, region = "USA", STATE_TYPO_FIXES.get(header_name, header_name)
        else:
            country, region = header_name, None

        for line in block.split("\n"):
            line = line.strip()
            if not line or line == "<hr />":
                continue
            artist = parse_artist_line(line)
            if artist:
                # A couple of index entries are literally named "Techspressionism"
                # / "Abstract Techspressionism" -- a site data-entry mistake
                # (someone's real name got replaced with an exhibition/movement
                # title), not a person's name. Left in, these poison Stage 4's
                # name-matching: "Abstract Expressionism" -- a real, distinct,
                # well-known art movement -- nearly got corrected into "Abstract
                # Techspressionism" at 89% confidence before this filter existed.
                if "techspressionis" in artist["name"].lower():
                    continue
                artist["country"] = country
                artist["region"] = region
                artists.append(artist)
    return artists


def load_export(xml_path):
    tree = ET.parse(xml_path)
    channel = tree.getroot().find("channel")
    for item in channel.findall("item"):
        if item.findtext("wp:post_id", namespaces=NS) == "132":
            return item.findtext("content:encoded", namespaces=NS) or ""
    raise SystemExit("Could not find Artists page (post_id 132) in export")


def main():
    if len(sys.argv) != 2:
        print("Usage: 04a-parse-artist-index.py <path-to-wxr.xml>", file=sys.stderr)
        sys.exit(1)

    body = load_export(Path(sys.argv[1]))
    artists = parse_artists_page(body)

    with open(OUT_PATH, "w") as f:
        json.dump(artists, f, indent=2, ensure_ascii=False)

    countries = sorted(set(a["country"] for a in artists))
    print(f"Parsed {len(artists)} artists across {len(countries)} countries")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
