"""English Wikipedia text and pictures for artist pages (Colin 2026-09-24).

For each artist with a Wikipedia address (data/people.json links.wikipedia) this keeps, in data/wikipedia-artists.json:
  * the article's introduction as plain text (no links), with its revision id and the date it was retrieved;
  * the article's lead picture, ONLY when it is freely licensed (public domain, CC0 or a Creative Commons licence) and hosted on Wikimedia
    Commons, with author, licence and file page for the credit; the picture is saved (scaled to 640 px wide) in assets/artist-images/.
refresh() is called at the start of every build: one request asks Wikipedia for the current revision id of every article, and only articles
that changed since the last run are fetched again (their retrieval date becomes today). If Wikipedia cannot be reached the saved copy is used.
"""
import datetime
import html
import io
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "wikipedia-artists.json"
IMG_DIR = ROOT / "assets" / "artist-images"
API = "https://en.wikipedia.org/w/api.php"
UA = "TechspressionismVideoArchive/1.0 (https://techspressionism.com/archive/; artist pages)"
FREE = re.compile(r"^(cc[ -]?by|cc0|public domain|pd\b|pd-|attribution)", re.I)
MAX_TEXT = 1400


def _get(params):
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def _title(url):
    return urllib.parse.unquote(url.rsplit("/wiki/", 1)[-1]).replace("_", " ")


def _strip(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _clip(text):
    text = re.sub(r"\n{2,}", "\n\n", text.strip())
    if len(text) <= MAX_TEXT:
        return text
    cut = text[:MAX_TEXT]
    end = max(cut.rfind(". "), cut.rfind(".\n"))
    return (cut[:end + 1] if end > 400 else cut.rstrip() + "…")


def _picture(file_name, pid, refresh_image=True):
    """The credit + a saved copy for File:<file_name>, or None when it is not clearly free to reuse."""
    d = _get({"action": "query", "titles": "File:" + file_name, "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime",
              "iiurlwidth": 640, "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|Credit|Copyrighted|NonFree|Restrictions|AttributionRequired"})
    page = (d.get("query", {}).get("pages") or [{}])[0]
    if page.get("imagerepository") != "shared" or not page.get("imageinfo"):
        return None                       # only Wikimedia Commons files; a picture uploaded to English Wikipedia itself is often "fair use"
    ii = page["imageinfo"][0]
    meta = {k: _strip((v or {}).get("value")) for k, v in (ii.get("extmetadata") or {}).items()}
    lic = meta.get("LicenseShortName", "")
    if meta.get("NonFree") or meta.get("Restrictions") or not FREE.match(lic):
        return None
    thumb = ii.get("thumburl") or ii.get("url")
    if refresh_image or not (IMG_DIR / f"{pid}.jpg").exists():
        from PIL import Image
        req = urllib.request.Request(thumb, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            im = Image.open(io.BytesIO(r.read()))
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
            im = bg
        im = im.convert("RGB")
        if im.width > 640:
            im = im.resize((640, round(im.height * 640 / im.width)), Image.LANCZOS)
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        im.save(IMG_DIR / f"{pid}.jpg", "JPEG", quality=88, optimize=True, progressive=True)
        time.sleep(1)
    return {"file": file_name, "author": meta.get("Artist") or meta.get("Credit") or "", "license": lic, "license_url": meta.get("LicenseUrl", ""),
            "page": "https://commons.wikimedia.org/wiki/File:" + urllib.parse.quote(file_name.replace(" ", "_"))}


def _norm(n):
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\([^)]*\)", "", n.lower().replace("-", " "))).split()


def same_person(title, names):
    """True when the article title is one of the person's names (or contains / is contained in it): guards against a wrong or disambiguation address."""
    t = _norm(title)
    for n in names:
        w = _norm(n)
        if w and t and (w == t or (len(t) >= 2 and all(x in w for x in t)) or (len(w) >= 2 and all(x in t for x in w))):
            return True
    return False


def refresh(people, offline=False):
    """people: iterable of {"id", "name", "links": {"wikipedia": [urls]}}. Returns {id: entry}; entries carry text/image when available."""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    wanted, names = {}, {}
    for p in people:
        urls = (p.get("links") or {}).get("wikipedia") or []
        if urls:
            wanted[p["id"]] = _title(urls[0])
            names[p["id"]] = [p["name"]] + list(p.get("aliases") or [])
    if offline:
        return {k: v for k, v in cache.items() if k in wanted and "skip" not in v}
    today = datetime.date.today().isoformat()
    try:
        current = {}
        titles = list(dict.fromkeys(wanted.values()))
        for i in range(0, len(titles), 40):
            d = _get({"action": "query", "titles": "|".join(titles[i:i + 40]), "prop": "info", "redirects": 1})
            redirects = {r["from"]: r["to"] for r in d["query"].get("redirects", []) + d["query"].get("normalized", [])}
            for pg in d["query"]["pages"]:
                if not pg.get("missing"):
                    current[pg["title"]] = pg["lastrevid"]
            for a, b in redirects.items():
                if b in current:
                    current.setdefault(a, current[b])
            time.sleep(1)
        changed = [pid for pid, t in wanted.items() if t in current and cache.get(pid, {}).get("revid") != current[t]]
        # introductions of the changed articles, 20 at a time (the limit for full-text extracts)
        for i in range(0, len(changed), 20):
            batch = changed[i:i + 20]
            d = _get({"action": "query", "titles": "|".join(wanted[p] for p in batch), "prop": "extracts|pageimages|info|pageprops", "ppprop": "disambiguation", "exintro": 1, "explaintext": 1,
                      "exlimit": "max", "piprop": "name", "redirects": 1, "inprop": "url"})
            by_title = {pg["title"]: pg for pg in d["query"]["pages"] if not pg.get("missing")}
            alias = {r["from"]: r["to"] for r in d["query"].get("redirects", []) + d["query"].get("normalized", [])}
            for pid in batch:
                t = wanted[pid]
                pg = by_title.get(alias.get(t, t)) or by_title.get(t)
                if not pg or not pg.get("extract"):
                    continue
                if "disambiguation" in (pg.get("pageprops") or {}) or not same_person(pg["title"], names[pid]):
                    cache[pid] = {"skip": f"'{pg['title']}' is a disambiguation page or another person: check the Wikipedia address in data/wikipedia-links.json or artists.json",
                                  "title": pg["title"], "revid": pg["lastrevid"], "retrieved": today}
                    print(f"  skipped {pid}: Wikipedia address gives '{pg['title']}'")
                    continue
                entry = {"title": pg["title"], "url": pg.get("fullurl") or "https://en.wikipedia.org/wiki/" + urllib.parse.quote(pg["title"].replace(" ", "_")),
                         "revid": pg["lastrevid"], "retrieved": today, "text": _clip(pg["extract"])}
                pic = None
                if pg.get("pageimage"):
                    try:
                        pic = _picture(pg["pageimage"], pid)
                    except Exception as ex:
                        print(f"  picture for {pid}: {ex}")
                if pic:
                    entry["image"] = pic
                elif (IMG_DIR / f"{pid}.jpg").exists():
                    (IMG_DIR / f"{pid}.jpg").unlink()
                cache[pid] = entry
            time.sleep(1)
        for pid in list(cache):
            if pid not in wanted:
                del cache[pid]
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        print(f"Wikipedia: {len(wanted)} artists, {len(changed)} new or changed")
    except Exception as ex:
        print(f"Wikipedia could not be reached ({type(ex).__name__}: {ex}); using the saved copy")
    return {k: v for k, v in cache.items() if k in wanted and "skip" not in v}


if __name__ == "__main__":
    people = json.loads((ROOT / "data" / "people.json").read_text())
    people = people if isinstance(people, list) else people.get("people", people)
    res = refresh(people)
    for pid, e in sorted(res.items()):
        print(f"{pid:32} {'IMAGE ' + e['image']['license'] if e.get('image') else 'no free image'} | {len(e['text'])} chars")
