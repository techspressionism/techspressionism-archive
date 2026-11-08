#!/usr/bin/env python3
"""Artwork pictures for the artist pages, from the Southampton exhibition (Colin 2026-11-08), and birth year / place from Wikipedia.

Every artist who showed in "Techspressionism: Digital & Beyond" (Southampton Arts Center) has a page on techspressionism.com,
/exhibition/southampton/<name>/ (people.json ts_profile), with their work and its caption. For each of them this keeps:
  * data/southampton-art.json: {artist id: {profile, image (the address on techspressionism.com), file, caption}};
  * assets/southampton-art/<artist id>.jpg: the first captioned picture on that page, scaled to 1200 px at most.
For each artist with a Wikipedia address it also keeps, in data/wikipedia-births.json, {artist id: {year, place, title}}: the birth
year and birth place given in the article's infobox (or, failing that, in its first sentence, "born 1946, Frankfurt am Main, Germany").

The artist page shows the picture at the top, linked to the exhibition page, with the caption in small text below, and the birth line
under the name. Needs the network (run it on the Mac); re-run when the exhibition pages change, then commit the data and pictures.

    python3 scripts/fetch-southampton-art.py            # everything not fetched yet
    python3 scripts/fetch-southampton-art.py --all      # fetch every page again
"""
import html
import io
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "southampton-art.json"
BIRTHS = ROOT / "data" / "wikipedia-births.json"
IMG_DIR = ROOT / "assets" / "southampton-art"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
WIKI_UA = "TechspressionismVideoArchive/1.0 (https://techspressionism.com/archive/; artist pages)"
MAX_W = 1200


def get(url, ua=BROWSER_UA, binary=False):
    """urllib first; curl when the site turns urllib away (Cloudflare can)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=40) as r:
            data = r.read()
    except Exception:
        data = subprocess.run(["curl", "-sSL", "--fail", "-m", "60", "-A", ua, url], capture_output=True, check=True).stdout
    return data if binary else data.decode("utf-8", "replace")


def text_of(s):
    s = re.sub(r"<br\s*/?>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def best_src(img_tag, base):
    """The picture's address: the srcset size closest above 1200 px wide, else the largest, else src."""
    srcset = re.search(r'srcset="([^"]+)"', img_tag)
    cands = []
    if srcset:
        for part in srcset.group(1).split(","):
            bits = part.strip().split()
            if len(bits) == 2 and bits[1].endswith("w"):
                cands.append((int(bits[1][:-1]), bits[0]))
    if cands:
        cands.sort()
        above = [c for c in cands if c[0] >= MAX_W]
        url = (above[0] if above else cands[-1])[1]
    else:
        m = re.search(r'(?:data-src|src)="([^"]+)"', img_tag)
        url = m.group(1) if m else ""
    url = html.unescape(url)
    return urllib.parse.urljoin(base, url) if url else ""


def clean_caption(t):
    t = re.sub(r",?\s*\$\s?\d[\d,]*(\.\d\d)?", "", t)          # prices are left off, as on the artist pages' exhibition credits
    t = re.sub(r"\s+([,.])", r"\1", t)
    return re.sub(r"\.{2,}$", ".", t).strip()


def first_artwork(page, base):
    """(picture address, caption) of the first captioned picture in the post's content."""
    body = page
    m = re.search(r'<div[^>]+class="[^"]*(?:entry-content|post-content|et_pb_post_content|wp-block-post-content)[^"]*"', page)
    if m:
        body = page[m.start():]
    for pat in (r"<figure[^>]*>(.*?)</figure>",
                r'<div[^>]+class="[^"]*wp-caption[^"]*"[^>]*>(.*?)</div>'):
        for blk in re.finditer(pat, body, re.S):
            inner = blk.group(1)
            img = re.search(r"<img[^>]+>", inner)
            cap = re.search(r"<figcaption[^>]*>(.*?)</figcaption>", inner, re.S) or re.search(r'<p[^>]+class="[^"]*wp-caption-text[^"]*"[^>]*>(.*?)</p>', inner, re.S)
            if img and cap and len(text_of(cap.group(1))) > 15:
                return best_src(img.group(0), base), clean_caption(text_of(cap.group(1)))
    return "", ""


def save_picture(data, dest):
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGB")
        if im.width > MAX_W:
            im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
        im.save(dest, "JPEG", quality=86, optimize=True, progressive=True)
        return True
    except ImportError:
        pass
    tmp = dest.with_suffix(".src")
    tmp.write_bytes(data)
    if shutil.which("sips"):                     # macOS without Pillow
        r = subprocess.run(["sips", "-s", "format", "jpeg", "-Z", str(MAX_W), str(tmp), "--out", str(dest)], capture_output=True)
        tmp.unlink(missing_ok=True)
        return r.returncode == 0
    if data[:3] == b"\xff\xd8\xff":
        tmp.rename(dest)
        return True
    tmp.unlink(missing_ok=True)
    return False


# --- Wikipedia: birth year and place -------------------------------------------------------------------------------

def wiki_lead_wikitext(title):
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "parse", "page": title, "prop": "wikitext", "section": 0, "redirects": 1, "format": "json", "formatversion": 2})
    d = json.loads(get(url, ua=WIKI_UA))
    return (d.get("parse") or {}).get("wikitext") or ""


def strip_wiki(s):
    s = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"\{\{\s*(?:flag|flagcountry|flagicon)\s*\|([^|}]+)[^}]*\}\}", r"\1", s, flags=re.I)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", s)
    s = re.sub(r"<br\s*/?>", ", ", s)
    s = re.sub(r"<[^>]+>|'''?", "", s)
    s = re.sub(r"\s*,\s*(,\s*)+", ", ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip(" ,;")


def infobox_field(wt, name):
    m = re.search(r"\|[ \t]*" + name + r"[ \t]*=[ \t]*(.*?)(?=\n\s*\||\n\}\})", wt, re.S)
    return m.group(1).strip() if m else ""


def birth_from_wikitext(wt, lead_text=""):
    year, place = "", ""
    bd = infobox_field(wt, "birth_date")
    if bd:
        m = re.search(r"\{\{\s*birth[ _](?:date|year)[^|}]*\|\s*(?:[a-z]+\s*=\s*[^|]*\|\s*)*(\d{4})", bd, re.I) or re.search(r"\b(1[89]\d\d|20[0-2]\d)\b", bd)
        year = m.group(1) if m else ""
    bp = infobox_field(wt, "birth_place")
    if bp:
        place = strip_wiki(bp)
    if not (year and place) and lead_text:      # the first sentence: "(born 1946, Frankfurt am Main, Germany)", "(born October 12, 1951, in Buffalo, New York)"
        m = re.search(r"\(born ([^)]*)\)", lead_text)
        if m:
            inside = m.group(1)
            y = re.search(r"\b(1[89]\d\d|20[0-2]\d)\b", inside)
            if y and not year:
                year = y.group(1)
                after = inside[y.end():]
                p = re.match(r"\s*,?\s*(?:in\s+)?([A-Z][^;]+)$", after)
                if p and not place:
                    place = p.group(1).strip(" ,.")
            elif not y and not place:
                p = re.search(r"\bin ([A-Z][^;]+)$", inside)
                place = p.group(1).strip(" ,.") if p else ""
    if place and (len(place) > 80 or "|" in place or "=" in place or not re.search(r"[A-Za-z]", place)):
        place = ""
    return year, place


def main():
    again = "--all" in sys.argv
    people = json.loads((ROOT / "data" / "people.json").read_text())["people"]
    art = json.loads(OUT.read_text()) if OUT.exists() else {}
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    sh = [p for p in people if "/exhibition/southampton/" in (p.get("ts_profile") or "")]
    print(f"{len(sh)} artists with a Southampton exhibition page")
    missing = []
    for p in sh:
        pid, url = p["id"], p["ts_profile"]
        if not again and pid in art and (IMG_DIR / f"{pid}.jpg").exists():
            continue
        try:
            src, cap = first_artwork(get(url), url)
            if not src:
                missing.append((p["name"], "no captioned picture on the page"))
                continue
            if not save_picture(get(src, binary=True), IMG_DIR / f"{pid}.jpg"):
                missing.append((p["name"], "picture could not be saved: " + src))
                continue
            art[pid] = {"name": p["name"], "profile": url, "image": src, "file": f"{pid}.jpg", "caption": cap}
            print(f"  ok  {p['name']}: {cap[:70]}")
        except Exception as ex:
            missing.append((p["name"], f"{type(ex).__name__}: {ex}"))
        time.sleep(0.5)
    OUT.write_text(json.dumps(dict(sorted(art.items())), ensure_ascii=False, indent=1) + "\n")

    wiki = json.loads((ROOT / "data" / "wikipedia-artists.json").read_text())
    births = {}
    for p in people:
        w = wiki.get(p["id"])
        if not w or not w.get("title"):
            continue
        try:
            year, place = birth_from_wikitext(wiki_lead_wikitext(w["title"]), w.get("text", ""))
        except Exception as ex:
            print(f"  Wikipedia failed for {p['name']}: {ex}")
            year, place = birth_from_wikitext("", w.get("text", ""))
        if year or place:
            births[p["id"]] = {"year": year, "place": place, "title": w["title"], "url": w.get("url", "")}
        time.sleep(0.2)
    BIRTHS.write_text(json.dumps(dict(sorted(births.items())), ensure_ascii=False, indent=1) + "\n")

    print(f"\n{len(art)} artwork pictures kept in data/southampton-art.json; {len(births)} Wikipedia birth lines in data/wikipedia-births.json")
    for name, why in missing:
        print(f"  MISSING {name}: {why}")


if __name__ == "__main__":
    main()
