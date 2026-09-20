#!/usr/bin/env python3
"""Suggest a new website and Instagram address for artists on the artist index whose link is broken, parked or missing.

    python3 scripts/suggest-artist-links.py <export.xml or saved copy of the /artists/ page text> [--no-instagram]

Nothing is searched for on a search engine; three checks, each verified against the page itself:
  1. moved:      the old address still loads but now leads elsewhere (from the link check): the new address is the suggestion.
  2. linked:     an Instagram address printed on the artist's own working website or link page (high confidence).
  3. guessed:    the usual domain names for a name (firstlast.com, .art, .net, first-last.com, firstlaststudio.com ...) and Instagram
                 handles (firstlast, first.last, first_last, firstlastart ...); accepted only when the page names the artist
                 (website: first AND last name in the page; Instagram: both names in the profile's title). Website is HIGH when the
                 name is in the page title, else MEDIUM; a guessed Instagram is MEDIUM (a namesake is possible): a person must look.
Writes private/wp-artist-index/suggestions.json  {artist: [{kind, url, confidence, source}]}  for build-tvai-master-list.py.
Gentle: 8 domain lookups at a time, one Instagram request every 3 seconds (never more than 8 guesses per artist).
"""
import importlib.util
import json
import re
import socket
import sys
import threading
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "private" / "wp-artist-index" / "suggestions.json"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cil = load("cil", "check-index-links.py")
links = cil.links
PLATFORM_HANDLES = {"p", "explore", "reel", "reels", "accounts", "about", "tv", "stories", "direct", "techspressionism", "developer", "legal",
                    "squarespace", "opensea", "patreon", "wix", "wordpress", "shopify", "linktree", "foundation", "superrare", "vimeo", "youtube"}
TLDS = ["com", "art", "net", "org", "co", "studio", "online", "xyz", "me", "info", "design", "photography", "works", "gallery", "io", "us",
        "ca", "co.uk", "de", "fr", "nl", "it", "com.br"]
IG_LINK = re.compile(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]+)/?", re.I)


def ascii_(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def tokens(name):
    n = re.split(r"\s+(?:aka|AKA)\s+", re.sub(r'\([^)]*\)|"[^"]*"', "", name))[0]
    return re.sub(r"[^a-z ]", " ", ascii_(n)).split()


def site_candidates(t):
    f, l = t[0], t[-1]
    fl = f + l
    out = [f"{fl}.{tld}" for tld in TLDS]
    out += [f"{f}-{l}.com", f"{f}{l}art.com", f"{f}{l}studio.com", f"{f[0]}{l}.com", f"{l}art.com", f"art{fl}.com", f"{fl}artist.com",
            f"{fl}photography.com", f"{fl}design.com", f"{l}{f}.com", f"{fl}creative.com", f"{fl}gallery.com", f"{fl}.wixsite.com",
            f"{fl}.myportfolio.com", f"{fl}.format.com", f"{fl}.carrd.co", f"{l}.{f}.com"]
    return list(dict.fromkeys(out))


def ig_candidates(t):
    f, l = t[0], t[-1]
    return list(dict.fromkeys([f + l, f"{f}.{l}", f"{f}_{l}", f"{f}{l}art", f"{f}{l}_art", f"{f}.{l}.art", f"{f}{l}studio", f"{l}{f}"]))[:8]


def resolves(host):
    try:
        socket.setdefaulttimeout(4)
        socket.gethostbyname(host)
        return True
    except Exception:
        return False


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    box = cil.idx.get_box(args[0])
    rows, artists = cil.collect(box, with_artists=True)
    status = json.loads(links.STATUS.read_text())
    by = {}
    for a, _s, k, u in rows:
        by.setdefault(a, []).append((k, u, status.get(u, {})))
    found = json.loads(OUT.read_text()) if OUT.exists() and "--fresh" not in args else {}
    lock = threading.Lock()

    def add(artist, kind, url, conf, source):
        with lock:
            lst = found.setdefault(artist, [])
            if not any(x["kind"] == kind and x["url"].rstrip("/") == url.rstrip("/") for x in lst):
                lst.append({"kind": kind, "url": url, "confidence": conf, "source": source})

    need_w, need_i = [], []
    for artist, _sec in artists:
        L = by.get(artist, [])
        good = [(k, u) for k, u, s in L if s.get("status") in ("ok", "changed", "unverified")]
        bad = [(k, u) for k, u, s in L if s.get("status") in ("broken", "parked")]
        for k, u, s in L:                                              # 1. moved
            m = re.search(r"redirects? to (?:the front page )?(https?://\S+)", s.get("detail", ""))
            if k == "website" and s.get("status") == "changed" and m:
                add(artist, "website", m.group(1), "high", "the old address now redirects here")
        if not good or any(k == "website" for k, _u in bad):
            need_w.append(artist)
        if not good or any(k == "instagram" for k, _u in bad):
            need_i.append(artist)
        if any(k == "instagram" for k, _u in bad) or not good:         # 2. linked from a working page of theirs
            for k, u in good:
                if k in ("instagram", "twitter", "wikipedia") or any(x in u for x in ("x.com", "foundation.app", "opensea", "vimeo", "patreon")):
                    continue
                time.sleep(0.5)
                try:
                    _s, body, final = links.fetch(u)
                except Exception:
                    continue
                for h in set(IG_LINK.findall(body)):
                    if h.lower() not in PLATFORM_HANDLES:
                        add(artist, "instagram", f"https://www.instagram.com/{h}/", "high", f"linked from their working page {final}")
    print(f"{len(need_w)} artists need a website, {len(need_i)} an Instagram", flush=True)

    def try_site(job):                                                 # 3. guessed domains
        artist, host, t = job
        if not resolves(host):
            return
        try:
            _s, body, final = links.fetch("https://" + host + "/", timeout=12)
        except Exception:
            return
        v = links.judge_page(body, final, "https://" + host + "/")
        if v and v[0] in ("parked", "broken"):
            return
        low = ascii_(re.sub(r"<[^>]+>", " ", body))
        if not all(x in low for x in (t[0], t[-1])):
            return
        m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
        title = ascii_(m.group(1)) if m else ""
        conf = "high" if (t[0] in title and t[-1] in title) else "medium"
        add(artist, "website", final, conf, f"guessed domain {host}; the page names the artist")

    jobs = [(a, c, tokens(a)) for a in need_w if len(tokens(a)) >= 2 for c in site_candidates(tokens(a))]
    print(f"{len(jobs)} domain probes", flush=True)
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(try_site, jobs))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(found, indent=1, ensure_ascii=False))
    print(f"websites done: {sum(1 for v in found.values() if any(x['kind'] == 'website' for x in v))} artists have a suggested website", flush=True)

    if "--no-instagram" not in args:
        for n, artist in enumerate(need_i):
            t = tokens(artist)
            if len(t) < 2 or any(x["kind"] == "instagram" for x in found.get(artist, [])):
                continue
            for h in ig_candidates(t):
                time.sleep(3)
                url = f"https://www.instagram.com/{h}/"
                try:
                    _s, body, final = links.fetch(url)
                except Exception:
                    continue
                if "accounts/login" in final:
                    continue
                m = re.search(r'og:title" content="([^"]*)"', body)
                title = ascii_(re.sub(r"&#0?64;|&#x40;", "@", m.group(1))) if m else ""
                if "@" in title and t[0] in title and t[-1] in title:
                    add(artist, "instagram", url, "medium", f"guessed handle @{h}; the profile title names the artist (a namesake is possible)")
                    break
            if n % 10 == 9:
                OUT.write_text(json.dumps(found, indent=1, ensure_ascii=False))
                print(f"  Instagram guesses {n + 1}/{len(need_i)}", flush=True)
    OUT.write_text(json.dumps(found, indent=1, ensure_ascii=False))
    print(f"done: {len(found)} artists with at least one suggestion -> {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
