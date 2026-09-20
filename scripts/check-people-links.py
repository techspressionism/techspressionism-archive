#!/usr/bin/env python3
"""Check the websites and Instagram addresses in data/people.json and record which are broken.

    python3 scripts/check-people-links.py               # checks anything not checked in the last 30 days
    python3 scripts/check-people-links.py --recheck     # check everything again
    python3 scripts/check-people-links.py --only-broken # re-test just the ones found broken before

Writes  data/link-status.json  (address -> ok / broken / unverified, with the reason and the date)
and     review/broken-links.csv  (person, kind, address, problem): the list of links left out of the artist pages.
The site build (06-build-site.py) leaves out links marked broken; unverified ones (a site that refuses automated
requests, or an Instagram that could not be tested) are kept.

How it decides
  Websites: the address is fetched (following redirects, twice if it fails once). Not found (404, 410), server errors,
            a name that does not resolve, or no answer at all = broken. A refusal (401, 403, 429, 999) = unverified.
  Instagram: its status code is always 200, so the page itself is read: a real profile carries "Name (@handle)" in its
            title, a missing one only says "Instagram". A known-good profile is re-tested now and then, so that if
            Instagram starts limiting requests nothing is wrongly marked broken (the run pauses and retries).
Gentle by design: a few websites at once (never two requests to one site together), Instagram one request every 3 seconds.
"""
import csv
import datetime
import json
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "data" / "link-status.json"
REPORT = ROOT / "review" / "broken-links.csv"
UA = "Mozilla/5.0 (compatible; TVA-link-check; +https://techspressionism.github.io/techspressionism-archive/)"
CONTROL = "https://www.instagram.com/rozolution/"      # a profile known to exist
MAX_AGE_DAYS = 30
CTX = ssl.create_default_context()


class _Redirects(urllib.request.HTTPRedirectHandler):
    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_302      # older Python does not follow 308 itself


_OPENER = urllib.request.build_opener(_Redirects, urllib.request.HTTPSHandler(context=CTX))


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with _OPENER.open(req, timeout=timeout) as r:
        return r.status, r.read(400_000).decode("utf8", "ignore"), r.geturl()


def check_website(url):
    last = ""
    for attempt in range(2):
        try:
            status, _body, _final = fetch(url)
            return ("ok", f"HTTP {status}") if status < 400 else ("broken", f"HTTP {status}")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429, 999):
                return "unverified", f"HTTP {e.code} (refuses automated requests)"
            last = f"HTTP {e.code}"
            if e.code in (404, 410):
                return "broken", last
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, ConnectionError, OSError) as e:
            last = str(getattr(e, "reason", e))[:80]
        time.sleep(2)
    return "broken", last or "no answer"


def instagram_state(url):
    """'ok', 'missing' or 'unknown' (throttled/blocked)."""
    handle = urllib.parse.urlparse(url).path.strip("/").split("/")[0]
    try:
        status, body, final = fetch(url)
    except urllib.error.HTTPError as e:
        return "missing" if e.code == 404 else "unknown"
    except Exception:
        return "unknown"
    if "accounts/login" in final:
        return "unknown"
    m = re.search(r'og:title" content="([^"]*)"', body)
    title = re.sub(r"&#0?64;|&#x40;", "@", (m.group(1) if m else ""))
    if "@" in title and "Instagram" in title:
        return "ok"
    if re.search(r"<title>\s*Instagram\s*</title>", body):
        return "missing"
    return "unknown"


def main():
    args = sys.argv[1:]
    people = json.loads((ROOT / "data" / "people.json").read_text())["people"]
    old = json.loads(STATUS.read_text()) if STATUS.exists() else {}
    today = datetime.date.today()
    todo, owner = [], {}
    for p in people:
        for kind in ("website", "instagram"):
            for u in p["links"].get(kind, []):
                owner.setdefault(u, []).append((p["name"], kind))
    for u in owner:
        rec = old.get(u)
        fresh = rec and (today - datetime.date.fromisoformat(rec["checked"])).days < MAX_AGE_DAYS
        if "--only-broken" in args:
            if rec and rec["status"] == "broken":
                todo.append(u)
        elif "--recheck" in args or not fresh:
            todo.append(u)
    sites = [u for u in todo if "instagram.com" not in u]
    insta = [u for u in todo if "instagram.com" in u]
    print(f"{len(owner)} addresses; checking {len(sites)} websites and {len(insta)} Instagram profiles", flush=True)
    result = dict(old)
    lock = threading.Lock()
    hostlocks = {}

    def do_site(u):
        host = urllib.parse.urlparse(u).netloc.lower()
        with lock:
            hl = hostlocks.setdefault(host, threading.Lock())
        with hl:
            st, why = check_website(u)
        with lock:
            result[u] = {"status": st, "detail": why, "checked": today.isoformat()}
            if st != "ok":
                print(f"  {st:10} {u}  ({why})", flush=True)

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(do_site, sites))

    for i, u in enumerate(insta):
        if i % 20 == 0:                                   # a known-good profile must still look good, else Instagram is limiting us
            tries = 0
            while instagram_state(CONTROL) != "ok" and tries < 5:
                tries += 1
                print("  Instagram seems to be limiting requests; pausing 90 s", flush=True)
                time.sleep(90)
        state = instagram_state(u)
        if state == "missing":                            # a second look before calling it broken
            time.sleep(3)
            state = instagram_state(u)
        st = {"ok": ("ok", "profile found"), "missing": ("broken", "profile not found"),
              "unknown": ("unverified", "could not be tested")}[state]
        result[u] = {"status": st[0], "detail": st[1], "checked": today.isoformat()}
        if st[0] != "ok":
            print(f"  {st[0]:10} {u}  ({st[1]})", flush=True)
        time.sleep(3)

    STATUS.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    REPORT.parent.mkdir(exist_ok=True)
    with open(REPORT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["person", "kind", "address", "problem", "checked"])
        for u, rec in sorted(result.items()):
            if rec["status"] == "broken" and u in owner:
                for name, kind in owner[u]:
                    w.writerow([name, kind, u, rec["detail"], rec["checked"]])
    n_broken = sum(1 for u, r in result.items() if r["status"] == "broken" and u in owner)
    n_unv = sum(1 for u, r in result.items() if r["status"] == "unverified" and u in owner)
    print(f"done: {n_broken} broken (left out, listed in review/broken-links.csv), {n_unv} unverified (kept)", flush=True)


if __name__ == "__main__":
    main()
