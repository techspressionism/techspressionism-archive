#!/usr/bin/env python3
"""Build the TVAI master list: one row for EVERY artist on the Techspressionist Visual Artist Index (techspressionism.com/artists/),
with everything useful for keeping the index and the archive up to date and for contacting the artists.

    python3 scripts/build-tvai-master-list.py <export.xml or saved copy of the /artists/ page text> [--no-email-scan]
        [--mailchimp audience_export.csv] [--zoom registration_report.csv]... [--sac artists.xlsx]

Writes private/wp-artist-index/TVAI-master-list.csv (opens in Excel) with, per artist: whether they have NO WORKING LINKS, a public email
address and the page it was found on, country / state / city, the index section, the techspressionism.com profile page, the archive
page (final and test address), their activity in the archive (recordings, mentions, exhibitions), and for each link type
(website, Instagram, X / Twitter, Wikipedia, NFT, other) the working links, the BAD links (broken or parked, with the problem) and, for
website and Instagram, a suggested replacement with its confidence and source (from suggest-artist-links.py).

Emails come in two tiers, in this order: (1) found online, then (2) from Colin's own lists, newest first. The lists are given with
--mailchimp (a Mailchimp audience export: subscribed opt-ins, with the website / Instagram the artist entered), --zoom (a Zoom registration
report, cancelled registrations skipped; repeatable) and --sac (a spreadsheet of Last / First names, marked in its own column). A list row is
matched to an artist by exact name (or the Mailchimp ARTIST NAME) or by the same website or Instagram; a similar name (same last name and
first initial) goes in a separate "possible" column for a person to check, never in the email columns. Phone numbers and birthdays are never read.

Public emails: only an address printed on the artist's own working website (home page and up to two contact / about pages) or link page
(linktr.ee etc.), never guessed, with the page it came from. Nothing is emailed or stored elsewhere. THE FILE HOLDS PERSONAL DATA: it stays
in private/ (git-ignored) and must never be committed, published or added to the archive. (Using the addresses for a mailing list needs the
artists' consent under most privacy and anti-spam laws; a one-to-one message is different.)
Run check-index-links.py and suggest-artist-links.py first so the link statuses and suggestions are current.
"""
import csv
import difflib
import html as htmllib
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
DEST = ROOT / "private" / "wp-artist-index"
FINAL_BASE = "https://techspressionism.com/archive/"
TEST_BASE = "https://techspressionism.github.io/techspressionism-archive/"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cil = load("cil", "check-index-links.py")
links, idx = cil.links, cil.idx
US_STATES = ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii",
             "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
             "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
             "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
             "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "District of Columbia"]
REGION_COUNTRY = {"Canary Islands": "Spain"}          # index sections that are a region, not a country
PLATFORMS = ("instagram.com", "facebook.com", "twitter.com", "x.com", "foundation.app", "opensea.io", "vimeo.com", "youtube.com", "behance.net",
             "artstation.com", "saatchiart.com", "artsy.net", "wikipedia.org", "superrare", "objkt.com", "rarible.com", "fxhash.xyz", "patreon.com",
             "kalamint.io", "warpcast.com", "discord", "flickr.com", "linkedin.com", "tiktok.com", "medium.com")
JUNK_DOMAINS = ("example.", "sentry", "wixpress", "domain.com", "email.com", "yoursite", "yourdomain", "squarespace", "godaddy", "wordpress.",
                "schema.org", "w3.org", "cloudflare", "gravatar", "google.", "facebook.", "instagram.", "twitter.", "myportfolio", "adobe.",
                "weebly", "wix.com", "shopify", "sentry.io", "jquery", "fontawesome", "gstatic", "typekit", "protection")
EMAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
CONTACT_HINT = re.compile(r"contact|about|bio|cv|connect|hello|reach|info|mail", re.I)


PARTICLES = {"de", "da", "di", "do", "del", "della", "van", "von", "der", "den", "la", "le", "el", "al", "bin", "ben", "st", "mc", "dos", "das", "du"}
HANDLE_WORDS = re.compile(r"\b(studio|studios|team|collective|lab|labs|works|gallery|museum|foundation|inc|llc|project|projects|media|design|network|group|club)\b", re.I)


NAME_OVERRIDES = {"Le Chat Noir": ("", "Le Chat Noir"), "X New Worlds": ("", "X New Worlds"), "Delta N.A.": ("", "Delta N.A.")}     # not personal names
GENERIC_TAIL = {"artist", "art", "studio", "official"}


def tidy(s):
    """Names printed all in capitals or all in lower case (a form entry) get ordinary capitals; stray symbols are dropped."""
    s = re.sub(r"[*\u00ae\u2122:;()]+", "", s).strip()
    return s.title() if len(s) > 1 and (s.isupper() or s.islower()) else s


def split_name(display, first_last=None):
    """(first name, last name). A person's name splits at its last word (particles like 'de' or 'van' stay with it); a handle or a
    one-word name has no first name and goes entirely into the last-name column. A handle followed by a real name in brackets,
    "Fahn (James Gardiner)", uses the real name. A first/last pair from a list, when the artist matched one by exact name, is used
    as it stands (if it looks like a real pair)."""
    if display in NAME_OVERRIDES:
        return NAME_OVERRIDES[display]
    if first_last and re.search(r"[A-Za-z\u00c0-\u024f]{2,}", first_last[1]) and first_last[0].strip():
        return tidy(first_last[0]), tidy(first_last[1])
    inner = re.search(r"\(([^)]*)\)", display)
    n = re.sub(r"\s*\([^)]*\)", "", display)
    n = re.sub(r'\s*"[^"]*"|\s*\u201c[^\u201d]*\u201d', "", n)
    n = re.split(r"\s+(?:aka|AKA|a\.k\.a\.?)\s+", n)[0]
    n = re.sub(r"\s*/.*$", "", n)
    n = re.sub(r"[\u00ae\u2122]", "", n).strip()
    if inner and len(n.split()) == 1 and len(inner.group(1).split()) >= 2 and inner.group(1)[:1].isupper():
        n = inner.group(1).strip()                                # the real name given after a one-word handle
    toks = n.split()
    while len(toks) >= 3 and toks[-1].lower() in GENERIC_TAIL:
        toks = toks[:-1]                                          # "Beau Tardy Artist" -> Beau Tardy
    n = " ".join(toks)
    handle_like = (len(toks) < 2 or re.search(r"[0-9_]", n) or (len(toks) >= 3 and n == n.lower()) or HANDLE_WORDS.search(n)
                   or (len(toks) >= 2 and n == n.upper() and len(n) > 3))
    if handle_like:
        return "", n or display
    k = 2 if len(toks) >= 3 and toks[-2].lower().strip(".") in PARTICLES else 1
    return " ".join(toks[:-k]), " ".join(toks[-k:])


def canon_state(section):
    m = difflib.get_close_matches(section.strip().title(), US_STATES, n=1, cutoff=0.85)
    return m[0] if m else ""


def decode_cf(hexstr):
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except Exception:
        return ""


def emails_in(body):
    found = []
    text = htmllib.unescape(body).replace("&#64;", "@")
    for m in re.finditer(r'href=["\']mailto:([^"\'?]+)', text, re.I):
        found.append(urllib.parse.unquote(m.group(1)).strip())
    for m in re.finditer(r'data-cfemail="([0-9a-f]+)"', text, re.I):
        found.append(decode_cf(m.group(1)))
    plain = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    plain = re.sub(r"\s*[\[(]\s*at\s*[\])]\s*", "@", re.sub(r"<[^>]+>", " ", plain), flags=re.I)
    found += EMAIL.findall(plain)
    out = []
    for e in found:
        e = e.strip(".,;:<>()[]").lower()
        if not EMAIL.fullmatch(e) or e.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".woff", ".woff2")):
            continue
        if any(j in e.split("@")[1] for j in JUNK_DOMAINS) or e.split("@")[0] in ("user", "name", "email", "yourname", "you", "example", "test"):
            continue
        if e not in out:
            out.append(e)
    return out


def scan_site(url, fetch):
    """[(email, page)] from a site's home page and up to two contact / about pages of the same site."""
    try:
        _s, body, final = fetch(url)
    except Exception:
        return []
    res = [(e, final) for e in emails_in(body)]
    host = urllib.parse.urlparse(final).netloc.lower()
    extra = []
    for m in re.finditer(r'<a\s[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', body, re.I | re.S):
        href, label = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urllib.parse.urljoin(final, href)
        if urllib.parse.urlparse(full).netloc.lower() == host and (CONTACT_HINT.search(href) or CONTACT_HINT.search(label)) and full not in extra \
                and full.rstrip("/") != final.rstrip("/") and not re.search(r"\.(pdf|jpg|png|zip)$", full, re.I):
            extra.append(full)
    for page in extra[:2]:
        time.sleep(0.5)
        try:
            _s, b2, f2 = fetch(page)
        except Exception:
            continue
        res += [(e, f2) for e in emails_in(b2) if e not in [x for x, _ in res]]
    return res


def read_lists(args):
    """Records from the lists Colin supplies: {email, first, last, artist_name, website, instagram, when, source, optin}."""
    import datetime
    recs = []

    def opt(flag):
        return [args[i + 1] for i, a in enumerate(args) if a == flag and i + 1 < len(args)]
    for path in opt("--mailchimp"):
        for r in csv.DictReader(open(path, encoding="utf-8-sig", errors="replace")):
            em = (r.get("Email Address") or "").strip().lower()
            times = [x for x in (r.get("CONFIRM_TIME"), r.get("OPTIN_TIME"), r.get("LAST_CHANGED")) if x]
            when = max((datetime.datetime.fromisoformat(x) for x in times), default=datetime.datetime.min)
            if em:
                recs.append({"email": em, "first": r.get("First Name", ""), "last": r.get("Last Name", ""), "artist_name": r.get("ARTIST NAME", ""),
                             "website": " ".join(x for x in (r.get("Website"), r.get("SECONDARY WEBSITE")) if x), "instagram": r.get("Instagram", ""),
                             "when": when, "source": f"Mailchimp audience (subscribed, opt-in; last activity {when:%Y-%m-%d})", "optin": True})
    for path in opt("--zoom"):
        lines = open(path, encoding="utf-8-sig", errors="replace").read().splitlines()
        start = next((k for k, l in enumerate(lines) if "First Name" in l and "mail" in l.lower()), None)
        topic = " ".join(lines[3].split(",")[:1]) if len(lines) > 3 else "Zoom"
        m = re.search(r'"(\d\d/\d\d/\d{4})', lines[3]) if len(lines) > 3 else None
        event = f"{topic.strip().title()} {m.group(1)}" if m else "Zoom event"
        if start is None:
            continue
        for r in csv.DictReader(lines[start:]):
            if "cancel" in (r.get("Approval Status") or "").lower() or "denied" in (r.get("Approval Status") or "").lower():
                continue
            em = (r.get("Email") or "").strip().lower()
            try:
                when = datetime.datetime.strptime(r["Registration Time"].strip(), "%m/%d/%Y %I:%M:%S %p")
            except Exception:
                when = datetime.datetime.min
            if em:
                recs.append({"email": em, "first": r.get("First Name", ""), "last": r.get("Last Name", ""), "artist_name": "", "website": "", "instagram": "",
                             "when": when, "source": f"Zoom registration for {event} (registered {when:%Y-%m-%d}; not a mailing-list opt-in)", "optin": False})
    sac = []
    for path in opt("--sac"):
        import zipfile
        import xml.etree.ElementTree as ET
        z = zipfile.ZipFile(path)
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        ss = ["".join(x.text or "" for x in si.iter("{%s}t" % ns["m"])) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)] \
            if "xl/sharedStrings.xml" in z.namelist() else []
        for r in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter("{%s}row" % ns["m"]):
            cells = {}
            for c in r.findall("m:c", ns):
                v = c.find("m:v", ns)
                cells[re.match(r"[A-Z]+", c.get("r")).group(0)] = ss[int(v.text)] if c.get("t") == "s" and v is not None else (v.text if v is not None else "")
            if cells.get("A") and cells.get("B"):
                sac.append(f"{cells['B']} {cells['A']}")
    return recs, sac


def domain(u):
    h = urllib.parse.urlparse(u if "//" in u else "//" + u).netloc.lower().removeprefix("www.")
    return h


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    box = idx.get_box(args[0])
    rows, artists = cil.collect(box, with_artists=True)
    status = json.loads(links.STATUS.read_text())
    people_doc = json.loads((ROOT / "data" / "people.json").read_text())
    people = people_doc["people"]
    by_key = {}
    for p in people:
        for n in [p["name"]] + p.get("aliases", []):
            by_key.setdefault(idx.person_key(n), p)
    sug_path = DEST / "suggestions.json"
    suggestions = json.loads(sug_path.read_text()) if sug_path.exists() else {}
    b06 = load("b06", "06-build-site.py")                       # for the archive counts (who speaks, who is named, exhibitions)
    corpus = json.loads((ROOT / "corpus" / "corpus.json").read_text())
    b06.NAV_CORPUS[:] = corpus
    b06.load_people(corpus)
    live = {p["id"]: p for p in b06.PEOPLE}
    pages = {d.name for d in (ROOT / "site" / "artist").iterdir() if d.is_dir()} if (ROOT / "site" / "artist").is_dir() else set()

    # each entry: section, city, the techspressionism.com link on the name
    entries, section, cur = {}, "", None
    for line in box.split("\n"):
        s = line.strip()
        if s.startswith("<h3"):
            section = idx.plain(s)
            continue
        if not s or s.startswith("<hr"):
            continue
        name = idx.entry_name(line)
        if name and len(name) <= 70 and not line.lstrip().startswith(("</span>", "</strong>")):
            parts = idx.SPLIT.split(idx.plain(line), 1)
            city = re.sub(r"^[\s\-–—,]+", "", parts[1]).strip() if len(parts) > 1 else ""
            city = re.sub(r"^located:?\s*", "", city, flags=re.I).strip(" -\u2013\u2014,")
            city = "" if len(city) > 60 or city.upper() in ("USA", "US") else city
            m = re.search(r'<a\s[^>]*href="(https?://techspressionism\.com/[^"]*)"', line)
            if name not in entries:
                entries[name] = {"section": section, "city": city, "ts_link": m.group(1) if m else ""}

    by_artist = {}
    for a, sec, kind, url in rows:
        if (kind, url) not in by_artist.setdefault(a, []):
            by_artist[a].append((kind, url))

    # public emails from the artists' own working websites and link pages
    cache_path = DEST / ".email-cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = {}
    for a, _sec in artists:
        srcs = [u for k, u in by_artist.get(a, []) if k == "website" and status.get(u, {}).get("status") in ("ok", "changed") and not any(x in u for x in PLATFORMS)]
        srcs += [x["url"] for x in suggestions.get(a, []) if x["kind"] == "website" and x["confidence"] == "high"]
        todo[a] = list(dict.fromkeys(srcs))[:2]
    if "--no-email-scan" not in args:
        lock, hostlocks = threading.Lock(), {}
        jobs = [(a, u) for a, us in todo.items() for u in us if u not in cache]
        print(f"scanning {len(jobs)} websites for public email addresses", flush=True)

        def work(job):
            a, u = job
            host = urllib.parse.urlparse(u).netloc.lower()
            with lock:
                hl = hostlocks.setdefault(host, threading.Lock())
            with hl:
                res = scan_site(u, links.fetch)
            with lock:
                cache[u] = res
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(work, jobs))
        cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))

    recs, sac_names = read_lists(args)
    rec_by_key, rec_by_last = {}, {}
    for r in recs:
        for k in {idx.person_key(f"{r['first']} {r['last']}"), idx.person_key(r["artist_name"])} - {""}:
            rec_by_key.setdefault(k, []).append(r)
        full = idx.person_key(f"{r['first']} {r['last']}").split()
        if len(full) >= 2:
            rec_by_last.setdefault((full[-1], full[0][0]), []).append(r)
    sac_keys = {idx.person_key(n) for n in sac_names}
    columns = [("website", "website"), ("instagram", "Instagram"), ("twitter", "X / Twitter"), ("nft", "NFT"), ("social", "other")]
    head = ["review", "display name", "first name", "last name", "country", "city", "state or region", "NO WORKING LINKS (YES/NO)", "ALL EMAILS (found online first, then your lists newest first)", "email found online (public)",
            "online email source URL", "emails from your lists (newest first)", "list, date and how it matched", "POSSIBLE list matches (check by hand)",
            "on the Southampton (SAC) artists list", "section on the index",
            "TS website profile URL", "archive page (final address)", "archive page (test address)", "in the archive artist index (YES/NO)", "archive: recordings speaking", "archive: mentions",
            "exhibitions on techspressionism.com", "Wikipedia"]
    for kind, label in columns:
        head.append(f"{label} (working)")
        head.append(f"{label} (BAD)")
        if kind in ("website", "instagram"):
            head += [f"SUGGESTED {label}", f"suggested {label}: confidence and source"]
    head += ["address moved (still works; update to)", "could not be checked (open by hand)", "all bad links", "also known as", "notes"]
    out = []
    n_email = 0
    for artist, _sec in artists:
        ent = entries.get(artist, {"section": _sec, "city": "", "ts_link": ""})
        p = by_key.get(idx.person_key(artist))
        live_p = live.get(p["id"]) if p else None
        L = by_artist.get(artist, [])
        st = lambda u: status.get(u, {})
        good = [(k, u) for k, u in L if st(u).get("status") in ("ok", "changed", "unverified")]
        bad = [(k, u) for k, u in L if st(u).get("status") in ("broken", "parked")]
        section = ent["section"]
        state = canon_state(section) or (section if section in REGION_COUNTRY else "")
        country = "USA" if state in US_STATES else REGION_COUNTRY.get(section, section)
        city = ent["city"]
        if not city and p and p.get("location"):
            first = p["location"].split(",")[0].strip()
            city = first if first.lower() not in (section.lower(), state.lower()) and len(first) < 40 else ""
        emails = []
        for u in todo.get(artist, []):
            for e_, page in cache.get(u, []):
                if e_ not in [x for x, _ in emails]:
                    emails.append((e_, page))
        n_email += bool(emails)
        # your lists: exact name, the Mailchimp artist name, or the same website / Instagram; a similar name is only "possible"
        keys = {idx.person_key(x) for x in [artist] + (p.get("aliases", []) if p else [])} - {""}
        site_domains = {domain(u) for k, u in L if k == "website"} - {""}
        ig_handles = {urllib.parse.urlparse(u).path.strip("/").split("/")[0].lower() for k, u in L if k == "instagram"} - {""}
        hits = {}                                             # email -> {"when", "how", "notes": [..]}

        def hit(r, how):
            h = hits.setdefault(r["email"], {"when": r["when"], "how": how, "notes": [], "optin": r["optin"]})
            h["when"] = max(h["when"], r["when"])
            h["optin"] = h["optin"] or r["optin"]
            if how in ("exact name", "Mailchimp artist name") and "fl" not in h and r["first"].strip() and r["last"].strip():
                h["fl"] = (r["first"], r["last"])
            if r["source"] not in h["notes"]:
                h["notes"].append(r["source"])
        for k in keys:
            for r in rec_by_key.get(k, []):
                hit(r, "exact name" if idx.person_key(f"{r['first']} {r['last']}") == k else "Mailchimp artist name")
        for r in recs:
            if r["email"] in hits:
                continue
            ig = r["instagram"].strip().lstrip("@")
            ig = urllib.parse.urlparse(ig if "//" in ig else "//instagram.com/" + ig).path.strip("/").split("/")[0].lower()
            if any(domain(w) and domain(w) in site_domains for w in r["website"].split()) or (ig and ig in ig_handles):
                hit(r, "same website or Instagram")
        possible = []
        toks_ = idx.person_key(artist).split()
        if len(toks_) >= 2:
            for r in rec_by_last.get((toks_[-1], toks_[0][0]), []):
                if r["email"] not in hits and r["email"] not in [x[0] for x in possible]:
                    possible.append((r["email"], f"{r['first']} {r['last']}".strip(), r["source"]))
        if p is not None or artist:
            for em_, h in hits.items():                       # a website / Instagram the artist entered in Mailchimp, when theirs no longer works
                rec_ = next((r for r in recs if r["email"] == em_ and r["optin"]), None)
                if not rec_:
                    continue
                for w in rec_["website"].split():
                    if w.startswith("http") and not any(k == "website" for k, _u in good) and domain(w) not in {domain(u) for k, u in L if k == "website"} \
                            and links.check_website(w)[0] in ("ok", "changed"):
                        suggestions.setdefault(artist, []).append({"kind": "website", "url": w, "confidence": "high",
                                                                   "source": "entered by the artist in your Mailchimp list; link checked OK"})
                ig = rec_["instagram"].strip().lstrip("@")
                ig = "https://www.instagram.com/" + urllib.parse.urlparse(ig if "//" in ig else "//i/" + ig).path.strip("/").split("/")[0] + "/" if ig else ""
                if ig and not any(k == "instagram" for k, _u in good) and ig.rstrip("/").split("/")[-1].lower() not in ig_handles:
                    time.sleep(3)
                    if links.instagram_state(ig) == "ok":
                        suggestions.setdefault(artist, []).append({"kind": "instagram", "url": ig, "confidence": "high",
                                                                   "source": "entered by the artist in your Mailchimp list; profile found"})
        ordered = sorted(hits.items(), key=lambda kv: kv[1]["when"], reverse=True)
        first_last = next((h["fl"] for _e, h in ordered if "fl" in h), None)
        first_name, last_name = split_name(artist, first_last)
        list_emails = [e_ for e_, _h in ordered]
        online = [e_ for e_, _pg in emails]
        all_emails = online + [e_ for e_ in list_emails if e_ not in online]
        wiki = [u for k, u in L if "wikipedia.org" in u] or (p["links"].get("wikipedia", []) if p else [])
        row = {"review": "YES" if bad else "NO", "country": country, "city": city, "state or region": state, "display name": artist, "first name": first_name, "last name": last_name,
               "NO WORKING LINKS (YES/NO)": "YES" if not good else "NO", "ALL EMAILS (found online first, then your lists newest first)": "\n".join(all_emails),
               "email found online (public)": "\n".join(online), "online email source URL": "\n".join(pg for _, pg in emails),
               "emails from your lists (newest first)": "\n".join(list_emails),
               "list, date and how it matched": "\n".join(f"{h['how']}: {' | '.join(h['notes'])}" for _e, h in ordered),
               "POSSIBLE list matches (check by hand)": "\n".join(f"{n_} <{e_}> ({s_})" for e_, n_, s_ in possible[:4]),
               "on the Southampton (SAC) artists list": "YES" if keys & sac_keys else "", "section on the index": section,
               "TS website profile URL": (p or {}).get("ts_profile") or ent["ts_link"],
               "archive page (final address)": f"{FINAL_BASE}artist/{p['id']}/" if p and p["id"] in pages else "",
               "archive page (test address)": f"{TEST_BASE}artist/{p['id']}/" if p and p["id"] in pages else "",
               "in the archive artist index (YES/NO)": "YES" if p and p["id"] in pages else "NO",      # for an email list of just the artists shown in the archive's Artists index (Colin 2026-09-24)
               "archive: recordings speaking": len(live_p["speaks"]) if live_p else 0, "archive: mentions": len(live_p["mentions"]) if live_p else 0,
               "exhibitions on techspressionism.com": "; ".join(live_p["exhibition_labels"].get(s, {}).get("label", s) for s in live_p["exhibitions"]) if live_p else "",
               "Wikipedia": "\n".join(dict.fromkeys(wiki))}
        for kind, label in columns:
            row[f"{label} (working)"] = "\n".join(u for k, u in good if k == kind and st(u).get("status") != "changed")
            row[f"{label} (BAD)"] = "\n".join(f"{st(u).get('status', '').upper()}: {u} ({st(u).get('detail', '')})" for k, u in bad if k == kind)
            if kind in ("website", "instagram"):
                s_ = [x for x in suggestions.get(artist, []) if x["kind"] == kind]
                s_.sort(key=lambda x: {"high": 0, "medium": 1}.get(x["confidence"], 2))
                row[f"SUGGESTED {label}"] = "\n".join(x["url"] for x in s_[:2])
                row[f"suggested {label}: confidence and source"] = "\n".join(f"{x['confidence']}: {x['source']}" for x in s_[:2])
        row["address moved (still works; update to)"] = "\n".join(f"{u} -> {st(u).get('detail', '').replace('now redirects to ', '')}" for k, u in L if st(u).get("status") == "changed")
        row["could not be checked (open by hand)"] = "\n".join(u for k, u in L if st(u).get("status") == "unverified")
        row["all bad links"] = "\n".join(f"{k}: {st(u).get('status', '').upper()}: {u} ({st(u).get('detail', '')})" for k, u in bad)
        row["also known as"] = "; ".join(p.get("aliases", [])) if p else ""
        row["notes"] = "" if L else "no links on the index"
        out.append([row.get(h, "") for h in head])
    DEST.mkdir(parents=True, exist_ok=True)
    path = DEST / "TVAI-master-list.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(head)
        ix = {h: i for i, h in enumerate(head)}
        w.writerows(sorted(out, key=lambda r: (r[ix["country"]].lower(), r[ix["state or region"]].lower() if r[ix["country"]] == "USA" else "",
                                               r[ix["city"]].lower() if r[ix["country"]] != "USA" else "", r[ix["last name"]].lower(),
                                               r[ix["first name"]].lower(), r[ix["display name"]].lower())))
    print(f"wrote {len(out)} artists ({sum(1 for r in out if r[1])} with no working links, {n_email} with a public email) -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
