#!/usr/bin/env python3
"""Find English Wikipedia articles for the artists who have a page in the archive, and record the ones that are surely the
same person.

    python3 scripts/find-wikipedia-links.py            # the people who have an archive page (site/artist/<id>/)
    python3 scripts/find-wikipedia-links.py --all      # every person in data/people.json

For each person the name (and each alias) is looked up on Wikipedia. An article is ACCEPTED only when all of these hold:
it exists and is not a disambiguation page; its title is the person's name (a redirect from an alias is fine when the
title holds their first and last name); its first lines call them an artist, painter, sculptor, photographer, filmmaker ...;
and they also mention technology (digital, new media, video, computer, electronic, generative, internet, software ...) or
"Techspressionism". A namesake (a musician, an athlete) fails this. Accepted links go to data/wikipedia-links.json
(name -> address; build-people.py adds them to the artist's links). Articles found but not accepted are listed in
review/wikipedia-candidates.csv with the reason, for a person to decide; add the address by hand to
data/wikipedia-links.json to accept one. Nothing here changes a link that is already in people.json.
Gentle: one request every 0.4 s, with a contact address in the user agent.
"""
import csv
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "TVA-wikipedia-finder/1.0 (https://techspressionism.github.io/techspressionism-archive/; artist directory)"
ART = re.compile(r"\b(artist|painter|sculptor|photographer|filmmaker|film-maker|animator|illustrator|printmaker|installation|"
                 r"video art|new media|net art|media artist|graphic designer|multimedia|visual|conceptual|performance art)", re.I)
TECH = re.compile(r"\b(digital|new media|video|computer|electronic|generative|internet|software|algorithm|virtual|net art|"
                  r"technolog|robot|animation|3d|augmented|interactive|code|coding|Techspressionis)", re.I)
LINKS_PATH = ROOT / "data" / "wikipedia-links.json"


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()


def summary(title):
    url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_"), safe="") + "?redirect=true"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf8"))
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return None
        raise


def candidates_for(p):
    names = [p["name"]] + list(p.get("aliases", []))
    out = []
    for n in names:
        for part in re.split(r"\s+(?:aka|AKA|a\.k\.a\.?)\s+", n):
            part = re.sub(r"\s*\([^)]*\)", "", part).strip()
            if part and 1 < len(part.split()) <= 5 and part not in out and "/" not in part:
                out.append(part)
    return out[:4]


def judge(p, name, data):
    """(accepted, reason)"""
    if data.get("type") != "standard":
        return False, f"not an article ({data.get('type')})"
    title = data.get("title", "")
    keys = {norm(x) for x in [p["name"]] + list(p.get("aliases", [])) + candidates_for(p)}
    tk = norm(re.sub(r"\s*\([^)]*\)", "", title))
    words = norm(name).split()
    same_name = tk in keys or (len(words) >= 2 and words[0] in tk.split() and words[-1] in tk.split())
    if not same_name:
        return False, f"article is titled '{title}'"
    text = f"{data.get('description', '')}. {data.get('extract', '')}"
    if not ART.search(text[:600]):
        return False, "first lines do not call them an artist: " + (data.get("description") or "")[:70]
    if not TECH.search(text[:1500]):
        return False, "an artist, but no mention of technology or new media: " + (data.get("description") or "")[:70]
    return True, data.get("description") or ""


def main():
    everyone = "--all" in sys.argv
    people = json.loads((ROOT / "data" / "people.json").read_text())["people"]
    have_page = {d.name for d in (ROOT / "site" / "artist").iterdir() if d.is_dir()} if (ROOT / "site" / "artist").is_dir() else set()
    if not everyone and not have_page:
        sys.exit("Build the site first (06-build-site.py): the pages in site/ say who has an archive page.")
    known = json.loads(LINKS_PATH.read_text()) if LINKS_PATH.exists() else {}
    manual = {k: v for k, v in known.items() if not k.startswith("_")}
    rows, accepted = [], dict(manual)
    todo = [p for p in people if (everyone or p["id"] in have_page) and not p["links"].get("wikipedia") and p["name"] not in manual]
    print(f"looking up {len(todo)} people", flush=True)
    for i, p in enumerate(todo):
        for name in candidates_for(p):
            time.sleep(0.4)
            try:
                data = summary(name)
            except Exception as e:
                print(f"  {name}: {e}", flush=True)
                continue
            if not data:
                continue
            ok, why = judge(p, name, data)
            url = (data.get("content_urls", {}).get("desktop", {}) or {}).get("page", "")
            if ok:
                accepted[p["name"]] = url
                rows.append([p["name"], url, "accepted", why])
                break
            rows.append([p["name"], url, "review", why])
        if i % 50 == 49:
            print(f"  {i + 1}/{len(todo)}", flush=True)
    LINKS_PATH.write_text(json.dumps({"_comment": "Artist name -> English Wikipedia address. Written by find-wikipedia-links.py (accepted matches) and "
                                      "by hand (add a line to accept a candidate from review/wikipedia-candidates.csv). build-people.py adds these to the artists' links.",
                                      **dict(sorted(accepted.items()))}, indent=1, ensure_ascii=False) + "\n")
    out = ROOT / "review" / "wikipedia-candidates.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["artist", "wikipedia address", "result", "why"])
        w.writerows(r for r in rows if r[2] == "review" and r[0] not in accepted)
    print(f"accepted {sum(1 for r in rows if r[2] == 'accepted')} new links; {sum(1 for r in rows if r[2] == 'review' and r[0] not in accepted)} to review -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
