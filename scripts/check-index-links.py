#!/usr/bin/env python3
"""List the broken and parked links on the artist index of techspressionism.com, per artist, as a CSV to fix there.

    python3 scripts/check-index-links.py ~/Desktop/techspressionism.WordPress.<date>.xml [--max-age N]

Reads the artist index text box (the block with class "tvai" on /artists/) from a WordPress export, takes every icon link
(website, Instagram, Wikipedia, NFT, X/Twitter ...) with the artist it belongs to, tests each address (the same tests as
check-people-links.py: not found, server error, no such domain, a page that only says "not found", a parked or for-sale
domain; Instagram by reading the profile page), and writes into private/wp-artist-index/ :
    broken-links.csv       ONE row per artist who has a broken or parked link or no working link at all: artist, section (country or
                           state), NO WORKING LINKS (YES), how many links the index has, all bad links, then one column per link type
                           (website, Instagram, X / Twitter, Wikipedia, NFT, other) holding that type's bad address and the problem,
                           then the addresses that moved (still work; update) and the ones that could not be checked (open by hand)
A link that is not broken (working, moved, or unverified) never counts as bad. Results are shared with data/link-status.json, so a link checked recently (--max-age, default 1 day) is not fetched again.
Gentle: a few sites at once, never two requests to one site together, Instagram one request every 3 seconds.
"""
import csv
import datetime
import importlib.util
import json
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


links = load("cpl", "check-people-links.py")
idx = load("ewi", "export-wp-artist-index.py")
KIND_BY_ALT = {"wikipedia": "wikipedia", "website": "website", "instagram": "instagram", "twitter": "twitter", "rarible": "nft",
               "foundation": "nft", "opensea": "nft", "facebook": "social", "youtube": "social", "vimeo": "social"}


def kind_of(alt, url):
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.endswith("instagram.com") or "wikipedia.org" in host or host.endswith(("twitter.com", "x.com")):
        return "instagram" if host.endswith("instagram.com") else "wikipedia" if "wikipedia.org" in host else "twitter"
    a = (alt or "").lower()
    if a == "instagram":                      # the alt text is sometimes wrong (a Foundation address labelled "instagram")
        a = "website"
    for k, v in KIND_BY_ALT.items():
        if k in a:
            return v
    host = urllib.parse.urlparse(url).netloc.lower()
    return "instagram" if "instagram.com" in host else "wikipedia" if "wikipedia.org" in host else "twitter" if host.endswith(("twitter.com", "x.com")) else "website"


def collect(box, with_artists=False):
    """[(artist, section, kind, url)] for every icon link in the box (and, with_artists, also the list of every artist)."""
    out, section, artist, artists = [], "", "", []
    for line in box.split("\n"):
        s = line.strip()
        if s.startswith("<h3"):
            section = idx.plain(s)
            continue
        if not s or s.startswith("<hr"):
            continue
        name = idx.entry_name(line)
        if name and len(name) <= 70 and not line.lstrip().startswith(("</span>", "</strong>")):
            artist = name
            if (artist, section) not in artists:
                artists.append((artist, section))
        for m in re.finditer(r'<a\s[^>]*href="([^"]*)"[^>]*>\s*(?:<(?!img|/?a\b)[^>]+>\s*)*<img[^>]*?alt="([^"]*)"', line, re.S):
            url = m.group(1).strip()
            if url.startswith("http"):
                out.append((artist, section, kind_of(m.group(2), url), url))
    return (out, artists) if with_artists else out


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    links.MAX_AGE_DAYS = int(args[args.index("--max-age") + 1]) if "--max-age" in args else 1
    rows, artists = collect(idx.get_box(args[0]), with_artists=True)
    urls = sorted({u for _a, _s, _k, u in rows})
    old = json.loads(links.STATUS.read_text()) if links.STATUS.exists() else {}
    today = datetime.date.today()

    def fresh(u):
        r = old.get(u)
        return bool(r) and (today - datetime.date.fromisoformat(r["checked"])).days < links.MAX_AGE_DAYS \
            and (r.get("v") == links.VERSION or "instagram.com" in u)
    todo = [u for u in urls if not fresh(u)]
    sites = [u for u in todo if "instagram.com" not in u]
    insta = [u for u in todo if "instagram.com" in u]
    print(f"{len(rows)} icon links on the index, {len(urls)} addresses; checking {len(sites)} websites and {len(insta)} Instagram profiles", flush=True)
    result, lock, hostlocks = dict(old), threading.Lock(), {}

    def do_site(u):
        host = urllib.parse.urlparse(u).netloc.lower()
        with lock:
            hl = hostlocks.setdefault(host, threading.Lock())
        with hl:
            st, why = links.check_website(u)
        with lock:
            result[u] = {"status": st, "detail": why, "checked": today.isoformat(), "v": links.VERSION}

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(do_site, sites))
    print("websites done", flush=True)
    for i, u in enumerate(insta):
        if i % 20 == 0:
            tries = 0
            while links.instagram_state(links.CONTROL) != "ok" and tries < 5:
                tries += 1
                print("  Instagram seems to be limiting requests; pausing 90 s", flush=True)
                time.sleep(90)
        state = links.instagram_state(u)
        if state == "missing":
            time.sleep(3)
            state = links.instagram_state(u)
        st = {"ok": ("ok", "profile found"), "missing": ("broken", "profile not found"), "unknown": ("unverified", "could not be tested")}[state]
        result[u] = {"status": st[0], "detail": st[1], "checked": today.isoformat(), "v": links.VERSION}
        time.sleep(3)
        if i % 50 == 49:
            print(f"  Instagram {i + 1}/{len(insta)}", flush=True)
    links.STATUS.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")

    dest = ROOT / "private" / "wp-artist-index"
    dest.mkdir(parents=True, exist_ok=True)
    columns = [("website", "website"), ("instagram", "Instagram"), ("twitter", "X / Twitter"), ("wikipedia", "Wikipedia"), ("nft", "NFT"), ("social", "other")]
    by_artist = {}
    for artist, section in artists:                                   # every artist on the index, with or without links
        by_artist.setdefault(artist, {"section": section, "links": []})
    for artist, section, kind, url in rows:
        item = by_artist.setdefault(artist, {"section": section, "links": []})
        if (kind, url) not in item["links"]:
            item["links"].append((kind, url))
    out_rows, n_none = [], 0
    for artist, item in by_artist.items():
        bad_by_kind, changed, unverified, working = {}, [], [], 0
        for kind, url in item["links"]:
            r = result.get(url, {})
            st = r.get("status")
            line = f"{url} ({r.get('detail', '')})"
            if st in ("broken", "parked"):
                bad_by_kind.setdefault(kind, []).append(f"{st.upper()}: {line}")
            elif st == "changed":
                changed.append(line)
                working += 1
            elif st == "unverified":
                unverified.append(line)
            else:
                working += 1
        no_working = working == 0 and not unverified
        if not bad_by_kind and not no_working:
            continue
        n_none += no_working
        all_bad = [f"{k}: {x}" for k, xs in bad_by_kind.items() for x in xs]
        row = [artist, item["section"], "YES" if no_working else "", len(item["links"]) or "none on the index", "\n".join(all_bad)]
        row += ["\n".join(bad_by_kind.get(k, [])) for k, _label in columns]
        row += ["\n".join(changed), "\n".join(unverified)]
        out_rows.append(row)
    head = ["artist", "section (country or state)", "NO WORKING LINKS", "links on the index", "all bad links"] + [l for _k, l in columns] + \
           ["address moved (still works; update)", "could not be checked (open by hand)"]
    with open(dest / "broken-links.csv", "w", newline="", encoding="utf-8-sig") as f:      # utf-8-sig: opens correctly in Excel
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(sorted(out_rows, key=lambda r: r[0].lower()))
    print(f"done: {len(out_rows)} artists with a broken or parked link or no working links, {n_none} of them with no working link at all "
          f"-> {dest.relative_to(ROOT)}/broken-links.csv", flush=True)


if __name__ == "__main__":
    main()
