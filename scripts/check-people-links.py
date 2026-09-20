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
MAX_AGE_DAYS = 30                       # --max-age N changes it (the pre-push check uses 1)
VERSION = 3                             # v2 looks for parked domains and pages that only say "not found"; v3 also for placeholder pages and moved addresses
CTX = ssl.create_default_context()


class _Redirects(urllib.request.HTTPRedirectHandler):
    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_302      # older Python does not follow 308 itself


_OPENER = urllib.request.build_opener(_Redirects, urllib.request.HTTPSHandler(context=CTX))


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with _OPENER.open(req, timeout=timeout) as r:
        return r.status, r.read(400_000).decode("utf8", "ignore"), r.geturl()


PARKED_HOSTS = ("sedoparking.com", "sedo.com", "hugedomains.com", "dan.com", "afternic.com", "parkingcrew.net", "bodis.com",
                "above.com", "domainmarket.com", "buydomains.com", "parklogic.com", "undeveloped.com", "squadhelp.com",
                "brandbucket.com", "uniregistry.com", "sav.com", "domainsponsor.com", "hostgator.com/domain", "dynadot.com/market")
PARKED_TEXT = re.compile(
    r"(this domain (name )?(is|may be) (for sale|available)|domain (name )?is for sale|buy this domain|the domain .{0,80} is for sale|"
    r"is parked|parked (free )?(at|by|domain|with)|domain parking|this (web ?)?page is parked|this domain has expired|"
    r"domain (has )?expired|inquire (about|on) this domain|make an offer (on|for) this domain|for sale by owner|"
    r"related searches|sponsored listings)", re.I)
SOFT_404_TITLE = re.compile(r"^\W*(error\s*)?(404|page not found|not found|site not found|domain not found|this site can.t be reached|"
                            r"account (has been )?suspended|website (is )?(suspended|disabled))\b", re.I)


SHORTENERS = ("bit.ly", "tinyurl.com", "goo.gl", "ow.ly", "t.co", "y.at", "linktr.ee", "lnk.bio")
SAME_SERVICE = {"discordapp.com": "discord.com", "warpcast.com": "farcaster.xyz", "twitter.com": "x.com"}      # renamed services, not moved artists


def registrable(host):
    h = host.lower().removeprefix("www.")
    parts = h.split(".")
    return ".".join(parts[-3:] if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") else parts[-2:])


def judge_page(body, final_url, original_url=""):
    """A page that loaded: 'parked' (a domain-for-sale, parking or placeholder page), 'broken' (it only says not found),
    'changed' (the address now leads somewhere else), else None."""
    fu = urllib.parse.urlparse(final_url)
    host = fu.netloc.lower().removeprefix("www.")
    target = (host + fu.path).lower()
    if any(h in target for h in PARKED_HOSTS):
        return "parked", f"parked or for-sale page ({host})"
    if len(body) < 1500 and re.search(r"location\.href\s*=\s*[\"']/lander", body):
        return "parked", "parked domain (redirects to the registrar's /lander page)"
    if len(body) < 1500 and "cgi-sys/defaultwebpage" in body:
        return "parked", "hosting placeholder page (no website set up)"
    if re.search(r"/(missing|not-?found|404)/?$", fu.path.lower()):
        return "broken", f"the site sends this address to its 'missing' page ({fu.path})"
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    if title and SOFT_404_TITLE.search(title):
        return "broken", f"page title says: {title[:60]}"
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
    text = re.sub(r"<[^>]+>", " ", text)
    hit = PARKED_TEXT.search(text[:60000])
    if hit and len(text.split()) < 900:                   # a real site that merely mentions "for sale" is long
        return "parked", f"looks like a parked domain ('{hit.group(0)[:40]}')"
    if title and re.match(r"^\W*(coming soon|under construction|default web ?site page|welcome to nginx|apache2? (ubuntu )?default page|"
                          r"index of /|site (is )?not (yet )?published|website expired)", title, re.I) and len(text.split()) < 80:
        return "parked", f"placeholder page ('{title[:40]}')"
    if original_url:
        orig = urllib.parse.urlparse(original_url)
        o_host = orig.netloc.lower()
        if not any(o_host.endswith(s) for s in SHORTENERS):
            a, b = registrable(o_host), registrable(fu.netloc)
            if SAME_SERVICE.get(a) != b and a != b:
                return "changed", f"now redirects to {final_url}"
            if len(orig.path.strip("/")) >= 1 and fu.path.strip("/") == "" and not fu.query and a == b:
                return "changed", f"redirects to the front page {final_url} (this page or profile seems gone)"
    return None


def check_website(url):
    last = ""
    for attempt in range(2):
        try:
            status, _body, _final = fetch(url)
            if status >= 400:
                return "broken", f"HTTP {status}"
            verdict = judge_page(_body, _final, url)
            if verdict:
                return verdict
            return "ok", f"HTTP {status}"
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
    global MAX_AGE_DAYS
    args = sys.argv[1:]
    if "--max-age" in args:
        MAX_AGE_DAYS = int(args[args.index("--max-age") + 1])
    people_doc = json.loads((ROOT / "data" / "people.json").read_text())
    people = people_doc["people"]
    old = json.loads(STATUS.read_text()) if STATUS.exists() else {}
    today = datetime.date.today()
    todo, owner = [], {}
    for p in people:
        for kind, urls in p["links"].items():
            for u in urls:
                owner.setdefault(u, []).append((p["name"], kind))
    # every other address the pages send a reader to: the techspressionism.com profile and exhibition pages, the recording
    # pages on techspressionism.com, and the YouTube video of every recording
    for p in people:
        if p.get("ts_profile"):
            owner.setdefault(p["ts_profile"], []).append((p["name"], "techspressionism.com profile"))
    for slug_, info in people_doc.get("exhibitions", {}).items():
        if info.get("url"):
            owner.setdefault(info["url"], []).append((f"exhibition: {info.get('label', slug_)}", "exhibition page"))
    pages_file = ROOT / "data" / "site-pages.json"
    if pages_file.exists():
        for slug_, u in json.loads(pages_file.read_text()).get("pages", {}).items():
            owner.setdefault(u, []).append((slug_, "recording page on techspressionism.com"))
    corpus_file = ROOT / "corpus" / "corpus.json"
    if corpus_file.exists():
        for ent in json.loads(corpus_file.read_text()):
            owner.setdefault(ent["url"], []).append((f"{ent.get('type', 'salon')}-{int(ent['number']):03d}", "YouTube video"))
    for u in owner:
        rec = old.get(u)
        fresh = rec and (today - datetime.date.fromisoformat(rec["checked"])).days < MAX_AGE_DAYS \
            and (rec.get("v") == VERSION or "instagram.com" in u)
        if "--only-broken" in args:
            if rec and rec["status"] in ("broken", "parked"):
                todo.append(u)
        elif "--recheck" in args or not fresh:
            todo.append(u)
    sites = [u for u in todo if "instagram.com" not in u and "youtube.com/watch" not in u]
    videos = [u for u in todo if "youtube.com/watch" in u]
    insta = [u for u in todo if "instagram.com" in u]
    print(f"{len(owner)} addresses; checking {len(sites)} websites, {len(videos)} YouTube videos and {len(insta)} Instagram profiles", flush=True)
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
            result[u] = {"status": st, "detail": why, "checked": today.isoformat(), "v": VERSION}
            if st != "ok":
                print(f"  {st:10} {u}  ({why})", flush=True)

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(do_site, sites))
    for u in videos:                                       # a YouTube video: its oEmbed answer says whether it can still be watched
        time.sleep(0.5)
        try:
            status, _b, _f = fetch("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(u, safe=""))
            st, why = ("ok", "video available") if status == 200 else ("unverified", f"HTTP {status}")
        except urllib.error.HTTPError as e:
            st, why = (("broken", "video not found, private or removed") if e.code in (400, 401, 403, 404) else ("unverified", f"HTTP {e.code}"))
        except Exception as e:
            st, why = "unverified", str(e)[:60]
        result[u] = {"status": st, "detail": why, "checked": today.isoformat(), "v": VERSION}
        if st != "ok":
            print(f"  {st:10} {u}  ({why})", flush=True)

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
        result[u] = {"status": st[0], "detail": st[1], "checked": today.isoformat(), "v": VERSION}
        if st[0] != "ok":
            print(f"  {st[0]:10} {u}  ({st[1]})", flush=True)
        time.sleep(3)

    STATUS.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    REPORT.parent.mkdir(exist_ok=True)
    with open(REPORT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["person", "kind", "address", "problem", "checked"])
        for u, rec in sorted(result.items()):
            if rec["status"] in ("broken", "parked") and u in owner:
                for name, kind in owner[u]:
                    w.writerow([name, kind, u, rec["detail"], rec["checked"]])
    n_broken = sum(1 for u, r in result.items() if r["status"] in ("broken", "parked") and u in owner)
    n_unv = sum(1 for u, r in result.items() if r["status"] == "unverified" and u in owner)
    print(f"done: {n_broken} broken (broken or parked; left out, listed in review/broken-links.csv), {n_unv} unverified (kept)", flush=True)


if __name__ == "__main__":
    main()
