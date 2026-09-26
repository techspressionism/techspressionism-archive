#!/usr/bin/env python3
"""Build data/people.json: one record per person (artist, speaker) with what is publicly known about them, merged from

  * the artist index on techspressionism.com (data/artists.json: location, website, Instagram ...),
  * the exhibition microsites in a WordPress export (artist lists, the 183 Southampton artist pages with their work
    credit lines, and the exhibition reels, whose artists are listed with timecodes),
  * the people identified as speakers in the archive (corpus/corpus.json).

Facts only (names, places, links, work credit lines, reel timecodes); no biographies, no images, no emails.
The WordPress export is NOT kept in the repository (it holds drafts and private pages): pass its path.

    python3 scripts/build-people.py ~/Desktop/techspressionism.WordPress.2026-09-20.xml

Re-run after a new export or after the corpus changes; commit data/people.json.
"""
import base64
import html
import json
import re
import sys
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from lib_speakers import canonical_name, is_not_speaker  # noqa: E402

NS = {"wp": "http://wordpress.org/export/1.2/", "content": "http://purl.org/rss/1.0/modules/content/"}
SITE = "https://techspressionism.com"
ICONS = {"wikipedia": "wikipedia", "website": "website", "instagram": "instagram", "rarible": "rarible", "twitter": "twitter",
         "facebook": "facebook", "linkedin": "linkedin", "opensea": "opensea", "youtube": "youtube", "vimeo": "vimeo", "foundation": "foundation"}
MICROSITES = {                      # slug -> label (page titles in the export are used where they are better)
    "southampton": "Techspressionism: Digital & Beyond (Southampton Arts Center)",
    "brooklyn": "Hello Brooklyn: Techspressionism 2024",
    "chelsea": "Hello Chelsea: Techspressionism 2025",
    "chicago": "Techspressionism Chicago",
    "mariniana": "Mariniana",
    "uzbekistan": "Hello Uzbekistan",
    "cyberiana": "Cyberiana",
}
REELS = {"southampton": "hdJNx7dFVqE", "brooklyn": "Mzad3l4Xwxk", "chelsea": "Mzad3l4Xwxk"}


def strip_handle(name):
    return re.sub(r"\s*\([^)]*\)", "", name or "").strip()


def norm(name):
    s = unicodedata.normalize("NFKD", strip_handle(name)).encode("ascii", "ignore").decode().lower()
    s = re.split(r"\s*/\s*", s)[0]
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()


# The same person under two names that the automatic rules cannot see (an "aka", a nickname, a misspelling):
# {the name used in the recordings: [the other names]}. Add a line when the artist list shows a person twice.
PERSON_MERGES = {
    "Max Dalí / Kamilla Kulova": ["Max Dalí Kamilla Kulova"],
    "Cynthia Beth Rubin": ["C B Rubin", "Cynthia B Rubin", "Cynthia Rubin"],
    "Systaime": ["Michaël Borras AKA Systaime"],
    "Beau Tardy": ["Beau Tardy Artist"],
    "Moonth": ["Michael Mesiats aka Moonth"],
    "SCARLETMOTIFF": ["Noel Apitta aka SCARLETMOTIFF"],
    "Skywaterr": ["Göksu Ilgaz Koçakcıgil AKA Skywaterr"],
    "Giovanna Sun": ["Dubwoman aka Giovanna Sun"],
    "Paul D. Miller aka DJ Spooky that Subliminal Kid": ["Paul D. Miller aka DJ Spooky"],
    "Reese Schroeder": ["Wiliam Reese Schroeder"],
    "Jan Swinburne": ["Jan Swinbume"],   # "rn" misread as "m" somewhere in a WP export source (a bare, dataless stub record)
}


# Wikipedia addresses on techspressionism.com that point at the wrong page, and the right one (found 24 Sep 2026: the index links Michael Rees to a
# "may refer to" page; the artist is Michael Rees (artist), sculptor / interactive computing, 1995 Whitney Biennial)
WIKIPEDIA_FIXES = {"https://en.wikipedia.org/wiki/Michael_Rees": "https://en.wikipedia.org/wiki/Michael_Rees_(artist)"}


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()).strip("-")


def dec_raw(s):
    def f(m):
        try:
            return urllib.parse.unquote(base64.b64decode(m.group(1)).decode("utf8"))
        except Exception:
            return ""
    return re.sub(r"\[vc_raw_html[^\]]*\]([A-Za-z0-9+/=]+)\[/vc_raw_html\]", f, s or "")


def text_of(s):
    s = re.sub(r"\[/?[a-z_0-9]+[^\]]*\]", " ", dec_raw(s))
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def load_export(path):
    items = ET.parse(path).getroot().find("channel").findall("item")
    out = []
    for it in items:
        g = lambda tag: (it.find(tag, NS).text if it.find(tag, NS) is not None else None)
        if g("wp:status") != "publish" or g("wp:post_type") not in ("post", "page"):
            continue
        out.append({"type": g("wp:post_type"), "link": (it.find("link").text or "").replace(SITE, ""),
                    "title": it.find("title").text or "", "content": g("content:encoded") or ""})
    return out


def tokens(content):
    """Text pieces and icon links in order: ('text', str) and ('link', kind, href)."""
    s = dec_raw(content)
    s = re.sub(r"\[/?[a-z_0-9]+[^\]]*\]", " ", s)
    pos, out = 0, []
    for m in re.finditer(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', s, re.S):
        out.append(("text", s[pos:m.start()]))
        alt = re.search(r'<img[^>]+alt="([^"]*)"', m.group(2))
        kind = (alt.group(1).strip().lower() if alt else "")
        out.append(("link", kind, html.unescape(m.group(1)), text_of(m.group(2))))
        pos = m.end()
    out.append(("text", s[pos:]))
    return out


ENTRY = re.compile(r"^\s*([A-ZÀ-Ý0-9@][^–—]{1,60}?)\s+[–—]\s+(.{2,60}?)\s*$")


def parse_microsite_artists(content):
    """'Name – City, ST' entries, each followed by icon links (website, Instagram, Wikipedia ...)."""
    entries, cur = [], None
    for tok in tokens(content):
        if tok[0] == "text":
            for line in re.split(r"[\n\r]+|<br\s*/?>|</p>|</h\d>|</div>", tok[1]):
                line = re.sub(r"<[^>]+>", " ", line)
                line = re.sub(r"\s+", " ", html.unescape(line)).strip()
                m = ENTRY.match(line)
                if m and (len(m.group(1).split()) <= 5 or re.search(r"\baka\b", m.group(1), re.I)):
                    cur = {"name": m.group(1).strip(" *"), "location": m.group(2).strip(), "links": {}}
                    entries.append(cur)
                elif m:
                    # a long entry line (a collective: "NPT / Negin Ehtesabian and Patrick Lichty"): its own icon links follow it, and must not
                    # be handed to the artist listed just before it (24 Sep 2026: that gave Gregory Little other people's websites and Instagram)
                    cur = {"name": "", "location": "", "links": {}}
        elif cur is not None and tok[1] in ICONS:
            cur["links"].setdefault(ICONS[tok[1]], []).append(tok[2])
    return entries


def parse_southampton_post(item):
    c = item["content"]
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", c, re.S)
    head = text_of(h1.group(1)) if h1 else item["title"]
    name = item["title"].strip()
    loc = re.sub(r"^" + re.escape(name), "", head).strip()
    loc = re.sub(r"\s*//\s*", ", ", loc).strip(" ,")
    credits = []
    for cap in re.findall(r"\[caption[^\]]*\](.*?)\[/caption\]", c, re.S):
        t = re.sub(r"<img[^>]*>", "", cap)
        t = text_of(t)
        if len(t) > 15:
            credits.append(t[:400])
    return name, loc, credits


def parse_reel(content):
    t = text_of(content)
    if "order of appearance" in t:
        t = t.split("order of appearance", 1)[1]
    out = []
    for m in re.finditer(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*(?://|–|-)?\s*(.+?)(?=\s\d{1,2}:\d{2}(?::\d{2})?\s|\s*$)", t):
        parts = m.group(1).split(":")
        secs = sum(int(x) * 60 ** i for i, x in enumerate(reversed(parts)))
        name = re.split(r"\s*(?://|–)\s*", m.group(2))[0].strip(" *")
        if 2 < len(name) < 50:
            out.append((name, secs))
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    export = load_export(Path(sys.argv[1]).expanduser())
    people = {}                                   # norm key -> record

    def person(name):
        key = norm(name)
        if not key:
            return None
        handle = re.search(r"\(([^)]*)\)", name)
        name = strip_handle(name)
        if key not in people:
            people[key] = {"name": name.strip(" *"), "aliases": set(), "location": "", "links": {}, "index_links": {}, "ts_profile": "",
                           "exhibitions": {}, "reels": []}
        rec = people[key]
        cleaned = name.strip(" *")
        # a spelling that differs from the canonical name only by capitalization ("renata Janiszewska" vs
        # "Renata Janiszewska") is the same name, not a real alias worth showing -- compare case-insensitively
        if cleaned.lower() != rec["name"].lower() and "//" not in name:
            rec["aliases"].add(cleaned)
        if handle and handle.group(1).strip():
            rec["aliases"].add(handle.group(1).strip())
        return rec

    def kind_of(url):
        host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
        if "instagram.com" in host: return "instagram"
        if "wikipedia.org" in host: return "wikipedia"
        if host in ("twitter.com", "x.com"): return "twitter"
        if any(h in host for h in ("rarible.com", "foundation.app", "opensea.io", "superrare.com", "objkt.com")): return "nft"
        if any(h in host for h in ("facebook.com", "linkedin.com", "youtube.com", "vimeo.com", "tiktok.com")): return "social"
        return "website"

    def merge_links(rec, links, field="links"):
        for k, v in (links or {}).items():
            for u in (v if isinstance(v, list) else [v]):
                u = (u or "").strip()
                if not u:
                    continue
                u = re.sub(r"^([a-zA-Z]+)://", lambda m: m.group(1).lower() + "://", u)   # "Https://x.com" -> broken relative
                                                                                            # link once rendered as an <a href> -- fix the scheme casing (source data typo, e.g. Chris Bly's site)
                u = WIKIPEDIA_FIXES.get(u.rstrip("/"), u)
                u = re.sub(r"^(https?://)([^/]+)", lambda m: m.group(1) + m.group(2).lower(), u)     # "en.Wikipedia.org" -> "en.wikipedia.org"
                key = kind_of(u)
                have = {x.rstrip("/").lower() for x in rec[field].get(key, [])}
                if u.rstrip("/").lower() not in have:
                    rec[field].setdefault(key, []).append(u)

    # 1. the artist index
    for a in json.loads((ROOT / "data" / "artists.json").read_text()):
        r = person(a["name"])
        if r is None:
            continue
        r["location"] = r["location"] or ", ".join(x for x in (a.get("location"), a.get("country")) if x)
        merge_links(r, a.get("links"))
        merge_links(r, a.get("links"), "index_links")      # what the artist index itself lists: the only links an artist page shows
        r["ts_profile"] = r["ts_profile"] or a.get("profile_url") or ""

    # 2. the microsites
    pages = {p["link"].rstrip("/"): p for p in export if p["type"] == "page"}
    exhibitions = {}
    for slug, label in MICROSITES.items():
        home = pages.get("/" + slug)
        exhibitions[slug] = {"label": label, "url": f"{SITE}/{slug}/", "reel_video": REELS.get(slug, ""), "n_artists": 0}
        art = pages.get(f"/{slug}/artists")
        if art:
            for e in parse_microsite_artists(art["content"]):
                r = person(e["name"])
                if r is None:
                    continue
                r["location"] = r["location"] or e["location"]
                merge_links(r, e["links"])
                r["exhibitions"].setdefault(slug, [])
                exhibitions[slug]["n_artists"] += 1
    for item in export:
        if item["type"] == "post" and item["link"].startswith("/exhibition/southampton/") and item["link"].count("/") >= 4:
            name, loc, credits = parse_southampton_post(item)
            r = person(name)
            if r is None:
                continue
            r["location"] = r["location"] or loc
            r["ts_profile"] = SITE + item["link"]
            r["exhibitions"].setdefault("southampton", [])
            exhibitions["southampton"]["n_artists"] += 1
            for c in credits:
                c = re.sub(r",?\s*\$\s?\d[\d,]*", "", c)
                if c not in r["exhibitions"]["southampton"]:
                    r["exhibitions"]["southampton"].append(c)
    seen_reel = set()
    for slug in ("southampton", "brooklyn", "chelsea"):
        page = pages.get(f"/{slug}/reel")
        if not page:
            continue
        for name, secs in parse_reel(page["content"]):
            key = (REELS[slug], norm(name), secs)
            if key in seen_reel:
                continue
            seen_reel.add(key)
            r = person(name)
            if r is not None:
                r["reels"].append({"video": REELS[slug], "t": secs, "exhibition": slug})
                r["exhibitions"].setdefault(slug, [])

    # 3. people identified as speakers in the archive
    corpus = json.loads((ROOT / "corpus" / "corpus.json").read_text())
    speakers = set()
    for e in corpus:
        for s in e["segments"]:
            sp = s.get("speaker")
            if sp and not is_not_speaker(sp):
                speakers.add(sp)
    for sp in sorted(speakers):
        person(sp)

    def absorb(rec, gone):
        rec["aliases"] |= gone["aliases"] | {gone["name"]}
        rec["location"] = rec["location"] or gone["location"]
        rec["ts_profile"] = rec["ts_profile"] or gone["ts_profile"]
        for kk, vv in gone["links"].items():
            for u in vv:
                if u.rstrip("/") not in {x.rstrip("/") for x in rec["links"].setdefault(kk, [])}:
                    rec["links"][kk].append(u)
        for kk, vv in gone.get("index_links", {}).items():
            for u in vv:
                if u.rstrip("/").lower() not in {x.rstrip("/").lower() for x in rec["index_links"].setdefault(kk, [])}:
                    rec["index_links"][kk].append(u)
        for ex, cr in gone["exhibitions"].items():
            rec["exhibitions"].setdefault(ex, []).extend(x for x in cr if x not in rec["exhibitions"][ex])
        rec["reels"] += gone["reels"]

    # "Malavika Andrew" and "Malavika Mandal Andrew" are one person: merge a shorter name into a longer one when the
    # first and last words agree and every word of the shorter name is in the longer one
    for key in sorted(people, key=lambda k: -len(k)):
        rec = people.get(key)
        if rec is None:
            continue
        toks = set(key.split())
        for other in list(people):
            if other == key or other not in people:
                continue
            o = other.split()
            k = key.split()
            if len(o) >= 2 and len(o) < len(k) and o[0] == k[0] and o[-1] == k[-1] and set(o) <= toks:
                absorb(rec, people.pop(other))
    # names the pages carry with a place or tagline after a dash ("Colin Goldberg - North Bennington, VT",
    # "Beau Tardy Artist - B...!") belong to the person named before the dash
    for key in list(people):
        rec = people.get(key)
        head = re.split(r"\s+-\s+", rec["name"], 1)[0] if rec else ""
        if rec and head != rec["name"] and norm(head) in people and norm(head) != key:
            absorb(people[norm(head)], people.pop(key))
    # the same person under an "aka" name, a nickname or a long form, decided by hand: {name used in the recordings: [other names]}
    for keep, others in PERSON_MERGES.items():
        rec = people.get(norm(keep))
        for other in others:
            gone = people.get(norm(other))
            if rec and gone is not None and gone is not rec:
                absorb(rec, people.pop(norm(other)))
    # Wikipedia articles found by find-wikipedia-links.py or added by hand (data/wikipedia-links.json)
    wp_path = ROOT / "data" / "wikipedia-links.json"
    if wp_path.exists():
        for name, url in json.loads(wp_path.read_text()).items():
            rec = people.get(norm(name))
            if rec is not None and not name.startswith("_"):
                have = {u.rstrip("/").lower() for u in rec["links"].get("wikipedia", [])}
                if url.rstrip("/").lower() not in have:
                    rec["links"].setdefault("wikipedia", []).append(url)
    # An artist page shows ONE link of each kind, taken from the artist index on techspressionism.com and only if it works (Colin, 24 Sep 2026).
    # The exhibition pages' own icon lists are not used: they had handed other artists' links to the artist listed above them (Steve Miller, Gregory Little ...).
    # Only a Wikipedia address may come from elsewhere (data/wikipedia-links.json), and only when the index has none.
    status = {}
    sp = ROOT / "data" / "link-status.json"
    if sp.exists():
        status = {k.lower(): v for k, v in json.loads(sp.read_text()).items()}
    def works(u):
        return status.get(u.lower(), {}).get("status") not in ("broken", "parked")
    for r in people.values():
        chosen = {}
        for kind, urls in r["index_links"].items():
            good = next((u for u in urls if works(u)), None)
            if good:
                chosen[kind] = [good]
        if "wikipedia" not in chosen and r["links"].get("wikipedia"):
            chosen["wikipedia"] = [r["links"]["wikipedia"][0]]
        r["links"] = chosen
    ov_path = ROOT / "data" / "link-overrides.json"
    if ov_path.exists():       # hand-set links win over the index's own (by artist id)
        overrides = {k: v for k, v in json.loads(ov_path.read_text()).items() if not k.startswith("_")}
        for r in people.values():
            ov = overrides.get(slugify(r["name"]))
            if ov:
                for kind, url in ov.items():
                    r["links"][kind] = [url]
    out = []
    used = set()
    for key in sorted(people):
        r = people[key]
        base = slugify(r["name"]) or "person"
        slug, n = base, 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        used.add(slug)
        out.append({"id": slug, "name": r["name"], "aliases": sorted(r["aliases"]), "location": r["location"], "links": r["links"],
                    "ts_profile": r["ts_profile"], "exhibitions": r["exhibitions"], "reels": sorted(r["reels"], key=lambda x: (x["video"], x["t"]))})
    out.sort(key=lambda p: (p["name"].split()[-1].lower(), p["name"].lower()))
    (ROOT / "data" / "people.json").write_text(json.dumps({"exhibitions": exhibitions, "people": out}, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(out)} people | with reel timecodes: {sum(1 for p in out if p['reels'])} | in an exhibition: "
          f"{sum(1 for p in out if p['exhibitions'])} | with a website: {sum(1 for p in out if p['links'].get('website'))}")
    print({k: v["n_artists"] for k, v in exhibitions.items()})


if __name__ == "__main__":
    main()
