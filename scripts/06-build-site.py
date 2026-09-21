#!/usr/bin/env python3
"""Stage 6 -- build the static search site into site/ from corpus/corpus.json.

Produces one HTML page per session plus an index page with a Pagefind
search UI (filters: type, year, speaker, country). Every transcript
segment is an anchor target and carries a deep-link to that exact second
of the YouTube video, so a search result lands you on the matching
passage with the video link right there.

After running this, index the output:

    npx -y pagefind --site site

(06-build-site.py calls that automatically unless --no-index is passed.)

Usage:
    python3 scripts/06-build-site.py [--no-index]
"""
import datetime
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import unicodedata
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_sentences import split_sentences
import lib_speakers
from lib_speakers import is_not_speaker
from lib_media import TYPES, label, slug  # noqa: E402
import lib_seo  # noqa: E402
import lib_voicehints  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORPUS_JSON = ROOT / "corpus" / "corpus.json"
SITE_DIR = ROOT / "site"
THUMBNAILS_SRC_DIR = ROOT / "assets" / "thumbnails"  # tracked in git -- CI has no access to raw/
THUMBNAILS_OUT_DIR = SITE_DIR / "thumbnails"
SMALL_THUMBS_SRC_DIR = ROOT / "assets" / "thumbnails-small"   # 240 px, for the sidebar lists (make-small-thumbnails.py)
SMALL_THUMBS_OUT_DIR = SITE_DIR / "thumbnails-small"

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def e(s):
    return html.escape(str(s) if s is not None else "")


# "Suggest a correction": a link after each passage that opens the Gravity Form on techspressionism.com with
# the recording, time and passage already filled in (Gravity Forms populates fields from the address).
# Hidden unless data/site-config.json sets suggest_url, so nothing changes on the site until that is set.
SITE_CONFIG = json.loads((ROOT / "data" / "site-config.json").read_text()) if (ROOT / "data" / "site-config.json").exists() else {}
SUGGEST_MAX = 1200  # characters of the passage carried in the address; a longer one is cut at a word boundary
# "▶ watch" links start a few seconds BEFORE the passage: speech timing is only accurate to about a second
# (a few seconds on recordings whose Zoom timing was converted), and a listener needs a beat of context.
# The printed timecode and the citation keep the exact time.
WATCH_LEAD_IN = max(0, int(SITE_CONFIG.get("watch_lead_in_seconds", 3)))
# the timecode pills seek the inline player to just before the paragraph's first word
# recordings listed newest first by number, except those named in data/site-config.json "list_at_bottom" (the oldest recording of a series,
# numbered later than it was recorded): they go to the bottom of their list
LIST_AT_BOTTOM = set(SITE_CONFIG.get("list_at_bottom", []))


def list_order(x):
    return (f"{x.get('type', 'salon')}-{int(x['number']):03d}" in LIST_AT_BOTTOM, -x["number"])


PILL_LEAD_IN = max(0.0, float(SITE_CONFIG.get("pill_lead_in_seconds", 1)))


# Each page links back to the recording's own page on techspressionism.com (data/site-pages.json).
_SITE_PAGES_PATH = ROOT / "data" / "site-pages.json"
SITE_PAGES = json.loads(_SITE_PAGES_PATH.read_text()).get("pages", {}) if _SITE_PAGES_PATH.exists() else {}


_LINK_STATUS_PATH = ROOT / "data" / "link-status.json"
LINK_STATUS = json.loads(_LINK_STATUS_PATH.read_text()) if _LINK_STATUS_PATH.exists() else {}


def link_ok(url):
    """False for an address the link check found broken or parked (data/link-status.json); such links are left out of the pages."""
    return LINK_STATUS.get(url, {}).get("status") not in ("broken", "parked")


def site_page_html(entry):
    address = SITE_PAGES.get(slug(entry))
    if address and not link_ok(address):
        address = None
    return (f' &middot; <a href="{e(address)}" target="_blank" rel="noopener" data-pagefind-ignore>'
            f'View on techspressionism.com</a>') if address else ""


def canonical_base():
    """The archive's final public address (data/site-config.json). The environment variable TVA_CANONICAL_BASE overrides it,
    to try a build with the final address (staging) without editing the config."""
    return (os.environ.get("TVA_CANONICAL_BASE") or SITE_CONFIG.get("canonical_base") or "").strip().rstrip("/")


def noindex():
    """True while search engines should not list the pages: data/site-config.json "beta_noindex", or the environment variable
    TVA_NOINDEX=1, which forces it for one build. The GitHub Pages test copy is built with TVA_NOINDEX=1 (see .github/workflows/deploy.yml),
    so it stays out of search results after the real site at techspressionism.com/archive/ has been opened to them."""
    return bool(SITE_CONFIG.get("beta_noindex")) or os.environ.get("TVA_NOINDEX") == "1"


_RECORDING_PAGE = re.compile(r"^((?:salon|interview|roundtable|presentation)-[0-9]{3})\.html$")
_ARTIST_PAGE = re.compile(r"^artist-(.+)\.html$")
ASSET_PREFIXES = ("style.css", "thumbnails/", "thumbnails-small/", "pagefind/", "data/", "times/", "transcripts/", "llms.txt", "sitemap.xml")


CSS_VERSION = ""      # set in main() from the stylesheet's content


def clean_path(target):
    """The address a page has on the site, without .html (a folder with an index page, like a WordPress page):
    salon-081.html -> salon-081/, artist-x.html -> artist/x/, about.html -> about/, index.html?type=Salon -> ?type=Salon.
    Anything else (an asset, an absolute address) is returned unchanged."""
    path, rest = re.match(r"^([^?#]*)(.*)$", target).groups()
    if path == "index.html":
        return rest
    if path == "about.html":
        return "about/" + rest
    m = _RECORDING_PAGE.match(path)
    if m:
        return m.group(1) + "/" + rest
    m = _ARTIST_PAGE.match(path)
    if m:
        return "artist/" + m.group(1) + "/" + rest
    return target


def nest(page_html, depth):
    """Make a finished page ready to sit `depth` folders below the site root: its links to other pages become clean addresses and every
    relative link and asset path gets the matching number of ../ in front. (Recording pages and About are one folder down, artist pages two.)"""
    root = "../" * depth
    home = root or "./"
    if CSS_VERSION:                                   # a changed stylesheet gets a new address, so visitors never see a stale one
        page_html = page_html.replace('href="style.css"', f'href="style.css?v={CSS_VERSION}"')

    def fix(target):
        if not target or re.match(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//|#|/|\{|')", target):
            return target
        path, rest = re.match(r"^([^?#]*)(.*)$", target).groups()
        if path == "index.html":
            return home + rest
        c = clean_path(target)
        if c != target:
            return root + c
        if path.startswith(ASSET_PREFIXES):
            return root + target
        return target
    page_html = re.sub(r'\b(href|src|action)="([^"]*)"', lambda m: f'{m.group(1)}="{fix(m.group(2))}"', page_html)
    return page_html.replace("location.href='index.html'", f"location.href='{home}'")


def write_page(rel_dir, page_html, depth):
    """Write site/<rel_dir>/index.html (the home page when rel_dir is empty)."""
    folder = SITE_DIR / rel_dir if rel_dir else SITE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(nest(page_html, depth))


def canonical_url(filename):
    base = canonical_base()
    return f"{base}/{clean_path(filename)}" if base else ""


def add_robots(page_html, filename=""):
    """Head tags that steer search engines. While the archive is in beta (data/site-config.json:
    "beta_noindex": true) every page asks not to be listed; it stays fully usable for anyone with the
    address. Set to false at launch. When "canonical_base" is set (the archive's final public address,
    e.g. https://techspressionism.com/archive/) every page also names its own canonical address, so a
    second copy of the site (such as the GitHub one) is never mistaken for the original."""
    tags = []
    if noindex():
        tags.append('<meta name="robots" content="noindex, nofollow">')
    canon = canonical_url("" if filename == "index.html" else filename)
    if canon:
        tags.append(f'<link rel="canonical" href="{e(canon)}">')
    if not tags:
        return page_html
    return page_html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n' + "\n".join(tags), 1)


def suggest_link(entry, start, paragraph):
    base = (SITE_CONFIG.get("suggest_url") or "").strip()
    if not base:
        return ""
    names = {"recording": "recording", "time": "time", "text": "passage", **SITE_CONFIG.get("suggest_params", {})}
    text = paragraph if len(paragraph) <= SUGGEST_MAX else paragraph[:paragraph.rfind(" ", 0, SUGGEST_MAX)]
    query = urlencode({names["recording"]: slug(entry), names["time"]: int(start), names["text"]: text})
    return (f'<a class="suggest" href="{e(base + ("&" if "?" in base else "?") + query)}" target="_blank" '
            f'rel="noopener" data-pagefind-ignore>Suggest a correction</a>')


def facet(s):
    """A filter value that won't break Pagefind's generated <label for=...>
    (quotes) or its comma-separated filter parsing."""
    s = str(s or "")
    for ch in '"“”‘’,':
        s = s.replace(ch, "" if ch != "," else " ")
    return e(re.sub(r"\s+", " ", s).strip())


def fmt_date(iso):
    """Chicago Manual of Style date order: Month Day, Year. Partial dates
    ("2023-03", "2000") stay partial rather than inventing a day."""
    if not iso:
        return "date unknown"
    try:
        parts = iso.split("-")
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return f"{MONTHS[int(parts[1])]} {parts[0]}"
        y, m, d = parts
        return f"{MONTHS[int(m)]} {int(d)}, {y}"
    except Exception:
        return iso


SERIES = {
    "salon": lambda n: f"Techspressionist Salon {n}",
    "interview": lambda n: f"Techspressionist Artist Interview Series #{n}",
    "roundtable": lambda n: f"Techspressionism Roundtable {int(n):02d}",
    "presentation": lambda n: "Hello Uzbekistan Presentations",
}
BRAND = "Techspressionism Video Archive"


def series_name(entry):
    if entry.get("series"):
        return entry["series"]
    return SERIES[entry.get("type", "salon")](entry["number"])


def date_is_estimate(entry):
    return "recording_date_estimated_from_upload" in (entry.get("flags") or [])


def participants_line(entry):
    """Who is speaking when a transcript carries no per-speaker labels."""
    if entry.get("interviewee"):
        return f"{entry['interviewee']}, interviewed by {entry['interviewer']}" if entry.get("interviewer") else entry["interviewee"]
    return ""


def hhmmss(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_browse(corpus, active="", navigate=False, sid=""):
    """'Browse' + a dropdown of the categories with counts. On the home page it swaps the list in place
    (script); on every other page (navigate=True) choosing one opens the home page on that category. There nothing is
    preselected: choosing the category the page belongs to would not fire a change event, so nothing would happen."""
    opts = "".join(
        f'<option value="{e(info["label"])}"{" selected" if info["label"] == active and not navigate else ""}>{e(info["plural"])} ({n})</option>'
        for info, n in [(TYPES[k], sum(1 for x in corpus if x.get("type", "salon") == k)) for k in TYPES] + [(ARTIST_ENTRY, ARTIST_COUNT)] if n)
    go = ' onchange="location.href=\'index.html\'+(this.value?\'?type=\'+encodeURIComponent(this.value):\'\')"' if navigate else ""
    return (f'<div class="browse"><label for="browse-select{sid}">BROWSE <span class="bslash">//</span></label>'
            f'<select id="browse-select{sid}"{go}><option value="">Choose a category&hellip;</option>{opts}</select></div>')


def load_wp_menu():
    """The techspressionism.com main menu (data/wp-menu.json, refreshed by scripts/refresh-wp-menu.py)."""
    path = ROOT / "data" / "wp-menu.json"
    return json.loads(path.read_text()).get("menu", []) if path.exists() else []


def build_wp_strip():
    """The strip at the top of every page: a way back to techspressionism.com and, behind the hamburger, its main menu as a
    full-screen flyout in the WordPress site's own look (black, pink links, red close)."""
    def items(menu):
        out = []
        for it in menu:
            sub = f'<ul>{items(it["children"])}</ul>' if it.get("children") else ""
            out.append(f'<li><a href="{e(it["url"])}">{e(it["label"])}</a>{sub}</li>')
        return "".join(out)
    menu = load_wp_menu()
    if not menu:
        return ""
    return ('<div class="wpstrip"><a class="wpbrand" href="https://techspressionism.com/" title="Back to techspressionism.com">Techspressionism</a>'
            '<button type="button" class="wptoggle" aria-controls="wpmenu" aria-expanded="false" aria-label="Open the Techspressionism menu"><i></i><i></i><i></i></button></div>\n'
            f'<nav id="wpmenu" class="wpmenu" aria-label="Techspressionism.com" hidden>'
            f'<button type="button" class="wpclose" aria-label="Close the menu"><i></i><i></i></button><ul>{items(menu)}</ul></nav>\n'
            + WP_MENU_JS + "\n")


WP_MENU_JS = """<script>
(function () {
  var t = document.querySelector('.wptoggle'), m = document.getElementById('wpmenu');
  if (!t || !m) return;
  var c = m.querySelector('.wpclose');
  function set(open) {
    m.hidden = !open; t.setAttribute('aria-expanded', String(open));
    document.documentElement.classList.toggle('wp-open', open);
    (open ? c : t).focus();
  }
  t.addEventListener('click', function () { set(true); });
  c.addEventListener('click', function () { set(false); });
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && !m.hidden) set(false); });
})();
</script>"""

WP_MENU_CSS = """
/* the strip that leads back to techspressionism.com, and its menu (the WordPress site's flyout look) */
.wpstrip { display:flex; align-items:center; justify-content:space-between; padding:.35rem 1.25rem; background:var(--card); border-bottom:1px solid var(--line); }
.wpbrand { font-family:"Kanit",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; font-style:italic; font-weight:800; font-size:1rem; letter-spacing:.02em; text-transform:uppercase; color:#000; text-decoration:none; }
.wpbrand:hover { color:var(--accent); text-decoration:none; }
.wptoggle { display:none; flex-direction:column; justify-content:center; gap:5px; width:2.2rem; height:2rem; padding:0 .3rem; border:0; background:none; cursor:pointer; }
.js .wptoggle { display:flex; }
.wptoggle i { display:block; height:2px; background:#000; }
.wptoggle:hover i { background:var(--accent); }
.wpmenu { position:fixed; inset:0; z-index:1000; background:#000; overflow-y:auto; padding:5rem 1.5rem 3rem; }
.wpmenu[hidden] { display:none; }
.wpmenu ul { list-style:none; margin:0 auto; padding:0; max-width:30rem; text-align:center; }
.wpmenu li { margin:0; padding:.5rem 0; }
.wpmenu li ul { padding-top:.4rem; }
.wpmenu a { font-family:"Open Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; font-size:18px; line-height:1.3; color:#dd73cb; text-decoration:none; }
.wpmenu a:hover, .wpmenu a:focus-visible { color:#fff; text-decoration:none; }
.wpclose { position:absolute; top:1.1rem; right:1.6rem; width:2.2rem; height:2.2rem; padding:0; border:0; background:none; cursor:pointer; }
.wpclose i { position:absolute; left:.1rem; right:.1rem; top:50%; height:2px; background:#f00; transform:rotate(45deg); }
.wpclose i + i { transform:rotate(-45deg); }
.wpclose:hover i, .wpclose:focus-visible i { background:#fff; }
html.wp-open { overflow:hidden; }
"""


def build_header(corpus, active="", sid="", strip=True):
    """The site header: title, [BETA], Browse, search box (the type pills are in the markup but hidden for now,
    see HIDE_PILLS_CSS). The SAME markup on every page, so it always looks the same. (On the home page a script
    wires the search box and Browse to the page.)"""
    return ((build_wp_strip() if strip else "") + '<header class="site"><div class="wrap"><strong><a href="index.html">Techspressionism Video Archive</a> '
            '<span class="beta">[BETA]</span></strong>\n'
            + build_topnav(corpus, active) + '\n'
            + build_browse(corpus, active, navigate=True, sid=sid) + '\n'
            + build_browse_links(corpus, active) + '\n'
            '<div class="hright"><form class="hsearch" action="index.html" method="get" role="search">'
            '<input type="search" name="q" placeholder="Search transcripts&hellip;" aria-label="Search transcripts" required></form></div>'
            '</div></header>')


FONT_LINKS = '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n<link href="https://fonts.googleapis.com/css2?family=Kanit:ital,wght@1,700;1,800&family=Lato:ital,wght@0,400;0,700;1,400;1,700&family=Open+Sans:wght@400&display=swap" rel="stylesheet">'
LISTED = []          # the people who have a page: those heard or named in the recordings
PEOPLE = []          # people directory (data/people.json) with archive statistics, set in main()
PERSON_BY_NORM = {}  # normalised name (or alias) -> person
ARTIST_COUNT = 0     # people listed by default under Artists (heard or mentioned in the recordings)
ARTIST_ENTRY = {"label": "Artist", "plural": "Artists"}
TIMES = {}           # slug -> sentence times for the search results (written to site/times/)
def build_browse_links(corpus, active=""):
    """Category links under the search box on the desktop home page (hidden everywhere else): red links
    separated by black double slashes."""
    links = []
    for key, info in list(TYPES.items()) + [("artist", ARTIST_ENTRY)]:
        if key == "artist" and ARTIST_COUNT or any(x.get("type", "salon") == key for x in corpus):
            links.append(f'<a href="index.html?type={info["label"]}" data-type="{info["label"]}" '
                         f'aria-current="{"true" if info["label"] == active else "false"}">{info["plural"]}</a>')
    sep = '<span class="bsep">//</span>'
    return '<nav class="browse-links" aria-label="Browse by category">' + sep.join(links) + '</nav>'


NAV_CORPUS = []      # set in main(): the header pills show a count per type


def build_topnav(corpus, active=""):
    """The type pills in the header, with counts. On the home page they filter in place (script below);
    on every other page they are links to the home page filtered to that type."""
    def chip(label, text, n, href):
        return (f'<a class="chip" href="{href}" data-type="{label}" aria-current="{"true" if label == active else "false"}">'
                f'{text}<span class="n">{n}</span></a>')
    chips = [chip("", "All", len(corpus), "index.html")]
    for info in TYPES.values():
        n = sum(1 for x in corpus if TYPES[x.get("type", "salon")]["label"] == info["label"])
        if n:
            chips.append(chip(info["label"], info["plural"], n, f'index.html?type={info["label"]}'))
    return f'<nav class="topnav" id="typebar" aria-label="Recording types">{"".join(chips)}</nav>'

STYLE = """
/* Type and colours follow techspressionism.com: headings Kanit italic, body Lato 17px black, links red and never underlined */
h1, h2, h3, h4, header.site strong { font-family:"Kanit",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; font-style:italic; }
h1 { font-weight:800; }
h2, h3, h4, header.site strong { font-weight:700; }
h3.para-time { font-family:inherit; font-weight:400; font-style:normal; }
:root { --fg:#000; --muted:#666; --bg:#fff; --card:#fff; --accent:#FF0000; --line:#e2e2e2; }
* { box-sizing: border-box; }
body { margin:0; font:17px/1.5 "Lato",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       color:var(--fg); background:var(--bg); }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:none; }
header.site { border-bottom:1px solid var(--line); background:var(--card); padding:.9rem 1.25rem; }
header.site .wrap { max-width:84rem; margin:0 auto; display:flex; gap:1rem; align-items:baseline; flex-wrap:wrap; }
header.site strong { font-size:1.1rem; white-space:nowrap; }
header.site strong a { color:inherit; }
header.site .wrap { container-type:inline-size; }
@media (max-width:63.99rem) {   /* phone/tablet layout (the desktop layout starts at 64rem, with nothing in between): the title fills the width of the screen on one line (17.85 = title + [BETA] length in em, plus a little slack) */
  header.site .wrap { row-gap:.1rem; }
  header.site strong { line-height:1.1; }
  header.site .topnav { flex:1 1 100%; justify-content:center; margin-top:.4rem; }   /* the pills fill the width, centred, in rows */
  header.site .topnav a.chip { flex:1 1 auto; text-align:center; }
  header.site .wrap > .d { display:none; }
  header.site strong { display:block; flex:1 1 100%; white-space:nowrap; font-size:6.4vw; font-size:min(calc(100cqw / 17.85), 2rem); line-height:1.2; }
}
.topnav { display:flex; flex-wrap:wrap; gap:.4rem; align-items:center; }
.topnav a.chip { font-size:.9rem; line-height:1.4; padding:.25rem .8rem; border:1px solid var(--line); background:var(--card); border-radius:1rem; color:var(--fg); }
.topnav a.chip:hover { border-color:var(--accent); color:var(--accent); }
.topnav a.chip[aria-current="true"] { background:var(--accent); border-color:var(--accent); color:#fff; }
.topnav .n { opacity:.7; font-size:.8em; margin-left:.3rem; }
header.site .hright { margin:0 0 0 auto; display:flex; flex-direction:column; gap:.4rem; }
header.site .hsearch { margin:0; }
header.site .browse { margin:0; gap:.6rem; }
@media (max-width:63.99rem) { header.site .wrap > .browse { flex:1 1 100%; margin-top:.5rem; } }
header.site .browse label { font-size:.9rem; }
header.site .browse select { padding:.3rem .7rem; font-size:.9rem; border-radius:1rem; background:var(--bg); }
header.site .hsearch input { font:inherit; font-weight:700; width:18rem; max-width:100%; height:2.9rem; padding:.4rem 1rem .4rem 2.8rem; border:2px solid var(--accent); border-radius:0; color:var(--fg);
  background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23666' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='10.5' cy='10.5' r='6.5'/%3E%3Cpath d='M15.5 15.5 21 21'/%3E%3C/svg%3E") no-repeat 1rem center / 1.2rem; }
header.site .hsearch input::placeholder { color:#757575; opacity:1; }
header.site .hsearch input::-webkit-search-cancel-button { cursor:pointer; }
header.site .hsearch input:focus { outline:none; border-color:var(--accent); }
@media (max-width:63.99rem) { header.site .hright { flex:1 1 100%; margin:.5rem 0 0; } header.site .hsearch input { width:100%; } }
main { max-width:60rem; margin:0 auto; padding:1.5rem 1.25rem 4rem; }
h1 { font-size:1.7rem; margin:.2rem 0 .3rem; }
h1 .topic { color:var(--muted); font-weight:400; }
.linkline { white-space:nowrap; font-size:min(1em, calc((100vw - 2.5rem) / 23.5)); }   /* one line on a phone (the text is about 22.2em wide) */
.meta { color:var(--muted); margin:.2rem 0 1.2rem; }
.speakers { list-style:none; padding:0; margin:0 0 1.5rem; display:flex; flex-wrap:wrap; gap:.4rem .8rem; }
.speakers li { background:var(--card); border:1px solid var(--line); border-radius:1rem; padding:.15rem .7rem; font-size:.9rem; }
.speakers .country { color:var(--muted); }
.flags { background:#fff8e1; border:1px solid #ffe08a; border-radius:.4rem; padding:.5rem .8rem; font-size:.88rem; color:#7a5c00; margin-bottom:1.5rem; }
section.seg { padding:.9rem 0; border-top:1px solid var(--line); }
.seg-head { display:flex; align-items:baseline; gap:.7rem; margin:0 0 .7rem; font-size:1rem; scroll-margin-top:calc(var(--title-h, 0px) + var(--player-h, 56.25vw) + 4.6rem); }
.seg-head .speaker { font-weight:inherit; }
.read-btn { display:none; width:100%; margin:0 0 1.2rem; padding:.85rem 1rem; border:2px solid var(--accent); border-radius:.4rem; background:var(--accent);
            color:#fff; font:inherit; font-size:1.05rem; font-weight:800; letter-spacing:.08em; text-transform:uppercase; cursor:pointer; }
.read-btn:hover { background:#d60000; border-color:#d60000; }
.read-btn[aria-expanded="true"] { background:#fff; color:var(--accent); }
.read-btn[aria-expanded="true"]:hover { background:#fff0f0; }
.js .read-btn { display:block; position:sticky; top:calc(var(--title-h, 0px) + var(--player-h, 56.25vw)); z-index:15; box-shadow:0 .5rem 0 var(--bg); }   /* narrow: locks to the bottom edge of the pinned video */
.js .transcript { display:none; scroll-margin-top:calc(var(--title-h, 0px) + var(--player-h, 56.25vw) + 4.6rem); }
.js .layout.reading .transcript { display:block; }
.layout.from-search .read-btn, .layout.reading .read-btn { display:none !important; }   /* Read transcript opens it for good; there is no Hide button */
@media (max-width:63.99rem) { .layout.reading .para, .layout.reading h3.para-time, .layout.reading .seg-head { scroll-margin-top:calc(var(--title-h, 0px) + var(--player-h, 56.25vw) + 1rem); } }
.watch-next { display:none; }
.watch-next h2 { font-size:1rem; margin:0 0 .6rem; }
.watch-next ul { list-style:none; margin:0; padding:0; max-height:calc(100vh - 6rem); overflow-y:auto; scrollbar-width:thin; border-top:1px solid var(--line); }
.watch-next li { border-bottom:1px solid var(--line); }
.watch-next a { display:flex; gap:.8rem; align-items:center; padding:.5rem .3rem; color:var(--fg); }
.watch-next img, .watch-next .nothumb { flex:none; width:96px; height:54px; border-radius:.25rem; background:#ddd; object-fit:cover; }
.watch-next .txt { min-width:0; }
.watch-next .t { display:block; }
.watch-next a:hover { background:var(--card); text-decoration:none; color:var(--accent); }
.watch-next li.cur a { background:#fdebc8; font-weight:600; }
.watch-next .num { color:var(--muted); font-variant-numeric:tabular-nums; }
.watch-next .d { display:block; color:var(--muted); font-size:.85rem; }
details.people { margin:0 0 1rem; }
details.people summary { cursor:pointer; color:var(--muted); font-size:.9rem; margin:0 0 .6rem; }
details.people .speakers { margin-bottom:.5rem; }
section.seg.cont { border-top:0; padding-top:0; }
section.seg.cont .speaker, .vh { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
section.seg.cont .seg-head { margin:0; }
/* transcript in the TED layout: a timecode pill above each paragraph; the video plays beside (wide) or above (narrow) */
main.watch-page { max-width:84rem; }
.side { display:contents; }   /* narrow: lets the sticky player stay pinned while the whole transcript scrolls */
.stickyheader { display:none; position:fixed; top:0; left:0; right:0; z-index:40; }
.stickyheader.on { display:block; }
.stickyheader header.site { padding-top:.6rem; padding-bottom:.6rem; }
.player-box { position:sticky; top:var(--title-h, 0px); z-index:20; background:#000; margin:0 -1.25rem 1rem; }
.player-frame { position:relative; aspect-ratio:16/9; background:#000; }
.player-frame iframe, .player-frame img { position:absolute; inset:0; width:100%; height:100%; border:0; object-fit:cover; }
.player-frame .poster { position:absolute; inset:0; width:100%; height:100%; padding:0; border:0; background:#000; cursor:pointer; }
.player-frame .bigplay { position:absolute; left:50%; top:50%; width:4.2rem; height:4.2rem; margin:-2.1rem 0 0 -2.1rem; border-radius:50%; background:rgba(240,240,240,.92); display:flex; align-items:center; justify-content:center; transition:transform .15s; }
.player-frame .poster:hover .bigplay, .player-frame .poster:focus-visible .bigplay { transform:scale(1.08); }
.player-frame .bigplay svg { width:1.5rem; height:1.7rem; margin-left:.25rem; fill:#111; }
.para { margin:0 0 1.5rem; scroll-margin-top:calc(var(--title-h, 0px) + var(--player-h, 56.25vw) + 4.6rem); }
h3.para-time { margin:0; font-size:1rem; font-weight:400; line-height:1.4; scroll-margin-top:calc(var(--title-h, 0px) + var(--player-h, 56.25vw) + 4.6rem); }
.para p { margin:.6rem 0 0; font-size:1.05rem; line-height:1.65; }
a.pill { display:inline-flex; align-items:center; gap:.4rem; background:#f0f0f0; color:#333; border-radius:1.2rem; padding:.22rem .8rem .22rem .62rem; font-size:.92rem; line-height:1.4; font-variant-numeric:tabular-nums; }
a.pill:hover { background:#e7e7e7; text-decoration:none; }
h3.para-time a.pill { padding:.22rem .8rem; }   /* the timecode alone, no triangle */
.cite-btn { display:none; font:inherit; font-size:.92rem; font-weight:700; line-height:1.4; margin-left:.4rem; padding:.22rem 1rem; border:0; border-radius:1.2rem; background:var(--accent); color:#fff; cursor:pointer; }   /* needs the script: shown only when it runs */
.js .cite-btn { display:inline-flex; align-items:center; }
.cite-btn:hover, .cite-btn[aria-expanded="true"] { background:#b30000; }
.para + .cite-card.para-cite { margin-top:-.7rem; scroll-margin-top:calc(var(--title-h, 0px) + var(--player-h, 56.25vw) + 3.6rem); scroll-margin-bottom:1rem; }
.cite-card .close-cite { font:inherit; font-size:.9rem; padding:.25rem .8rem; border:1px solid var(--line); border-radius:.3rem; background:#fff; color:var(--muted); cursor:pointer; }
.cite-card .close-cite:hover { border-color:var(--accent); color:var(--accent); }
a.pill svg { width:.72rem; height:.85rem; color:#8a8a8a; flex:none; }
a.pill:hover svg, a.pill:focus-visible svg { color:#FF0000; }
a.pill:hover svg path, a.pill:focus-visible svg path { fill:currentColor; }   /* solid red triangle on hover */
.para .tx { border-radius:.15rem; }
.para.active .tx { background:#fdebc8; -webkit-box-decoration-break:clone; box-decoration-break:clone; }
@media (min-width:64rem) {
  .layout { display:grid; grid-template-columns:minmax(0,1.7fr) minmax(24rem,1fr); gap:2.5rem; align-items:start; }
  .side { display:block; position:sticky; top:calc(var(--title-h, 0px) + 1rem); max-height:calc(100vh - var(--title-h, 0px) - 2rem); overflow:auto; scrollbar-width:thin; }
  .player-box { position:static; margin:0 0 1rem; border-radius:.4rem; overflow:hidden; }
  .para, .seg-head, h3.para-time, .js .transcript { scroll-margin-top:1.5rem; }
  .js .read-btn { position:static; box-shadow:none; }
  .js .watch-next { display:block; }
  .js .layout.reading .watch-next { display:none; }
}
a.suggest { font-size:.72rem; margin-left:.7rem; color:var(--muted); white-space:nowrap; opacity:.75; }
a.suggest:hover { opacity:1; color:var(--accent); }
/* index */
.sessions { list-style:none; padding:0; margin:.3rem 0 0; }
.sessions li { display:flex; gap:.6rem; border-bottom:1px solid var(--line); padding:.7rem 0; }
.sessions .num { flex:none; min-width:2.6rem; color:var(--muted); font-variant-numeric:tabular-nums; }
.sessions .body { min-width:0; }
.sessions .thumb { display:none; }
@media (min-width:64rem) { .sessions .thumb { display:block; flex:none; width:96px; height:54px; border-radius:.25rem; object-fit:cover; background:#ddd; } .sessions li { align-items:center; } }   /* desktop only: a thumbnail in each row, as in the sidebar list */
.sessions .d { display:block; color:var(--muted); font-size:.9rem; }   /* the date goes on its own line, aligned under the title */
#search { margin:.4rem 0 .3rem; }
.reccount { margin:.2rem 0 .6rem; color:var(--fg); }
/* the header search box replaces the widget's own input; the results live inside the widget's form, so hide only the input row */
#search .pagefind-ui__search-input, #search .pagefind-ui__search-clear { display:none; }
#search .pagefind-ui__form::before { display:none; }
body.searching #intro-block, body.searching .reccount, body.searching .sessions-group { display:none; }   /* while searching, the results come first */
.browse { display:flex; align-items:center; gap:.8rem; margin:.7rem 0 1rem; }
.browse[hidden] { display:none; }
.browse label { font:inherit; font-weight:700; letter-spacing:.03em; white-space:nowrap; }
.browse .bslash { color:var(--accent); }   /* same font as the intro line */
.browse select { flex:1; min-width:0; font:inherit; padding:.5rem .9rem; border:1px solid var(--line); border-radius:1rem; background:var(--card); color:var(--fg); }
#search .filters-toggle { display:none; width:100%; margin:.6rem 0 .2rem; padding:.45rem .9rem; border:1px solid var(--line); border-radius:1rem; background:var(--card); color:var(--fg); font:inherit; font-size:.95rem; cursor:pointer; align-items:center; justify-content:space-between; }
#search .filters-toggle:hover { border-color:var(--accent); }
@media (max-width:40rem) {   /* phones: filters collapsed behind one button */
  #search .filters-toggle { display:flex; }
  #search:not(.filters-open) .pagefind-ui__filter-panel { display:none; }
  #search .pagefind-ui__drawer { gap:.3rem; }
  #search .pagefind-ui__results-area { margin-top:0; }
  #search .pagefind-ui__message { padding-top:.5rem; padding-bottom:.5rem; }
}
.pagefind-ui { --pagefind-ui-scale:.9; --pagefind-ui-primary:var(--accent); --pagefind-ui-font:inherit; }
.pagefind-ui a, .pagefind-ui a:hover { text-decoration:none !important; }
.pagefind-ui mark { background:none; color:var(--accent); font-weight:700; padding:0; }
.beta { font-family:"Lato",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; font-style:normal; font-weight:700; font-size:.6em; color:var(--accent); margin-left:.4em; white-space:nowrap; }
.intro { color:var(--fg); max-width:44rem; margin:.2rem 0 .6rem; }
.tagline { font-size:inherit; }   /* same size as the About heading; lines fill the width (no balanced wrapping) */
.intro .watch-ref { color:var(--accent); font-weight:600; }
.yt-jump { white-space:nowrap; font-size:.85em; }
.citation-info { margin-top:.5rem; padding:.5rem .7rem; background:var(--bg); border:1px solid var(--line); border-radius:.35rem; font-size:.85em; color:#333; }
.citation-info strong { display:block; margin-bottom:.2rem; color:var(--muted); font-size:.85em; font-weight:600; }
.citation-info .cite-text { font-family:Georgia,"Times New Roman",serif; }
#search .cite-actions { display:flex; flex-wrap:wrap; align-items:center; gap:.4rem .5rem; margin-top:1rem; }
a.pill.pill-watch { background:var(--accent); color:#fff; font-weight:700; padding:.3rem 1.1rem; gap:.5rem; }
a.pill.pill-watch svg, a.pill.pill-watch:hover svg, a.pill.pill-watch:focus-visible svg { color:#fff; }
a.pill.pill-watch:hover { background:#d60000; }
a.pill.pill-watch .watch-word { letter-spacing:.05em; font-size:.8rem; }
#search .cite-actions .copy-cite, #search .cite-actions a.pill.pill-watch { box-sizing:border-box; height:2.2rem; padding-top:0; padding-bottom:0; display:inline-flex; align-items:center; line-height:1; }
#search .cite-actions a.pill { font-size:.85rem; padding:.18rem .65rem .18rem .55rem; }
.citation-info .copy-cite { display:block; margin:0; font:inherit; font-size:.85em; padding:.2rem .6rem; border:1px solid var(--line); background:var(--card); border-radius:.3rem; cursor:pointer; }
.citation-info .copy-cite:hover { border-color:var(--accent); color:var(--accent); }
.cite-format { margin-top:.6rem; font-size:.85rem; color:var(--muted); }
.cite-format select { font:inherit; font-size:.85rem; margin-left:.3rem; padding:.15rem .4rem; border:1px solid var(--line); background:var(--card); color:var(--fg); border-radius:0; }
.cite-text.cite-code { display:block; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:.8rem; white-space:pre-wrap; overflow-wrap:anywhere; margin-top:.2rem; }
.cite { margin:2.5rem 0 0; padding:1rem 1.1rem; background:var(--card); border:1px solid var(--line); border-radius:.5rem; }
.cite h2 { font-size:.95rem; margin:0 0 .5rem; }
.cite blockquote { margin:0; font-size:.92rem; color:#333; }
.cite button { margin-top:.6rem; font:inherit; font-size:.82rem; padding:.25rem .7rem; border:1px solid var(--line); background:var(--bg); border-radius:.3rem; cursor:pointer; }
.cite button:hover { border-color:var(--accent); color:var(--accent); }
.cite .doi { color:var(--muted); }

/* search-result citations and the citation card under a cited passage */
#search .citation-info { margin-top:.6rem; padding:.55rem 0 0; border:0; border-top:1px solid #bbb; background:none; border-radius:0; }   /* a thin rule between the quoted text and its citation */
.citation-info strong.cite-head, .cite-card strong.cite-head { color:var(--accent); display:block; margin:0 0 .25rem; font-size:inherit; }   /* the citation itself starts on the next line */
#search .copy-cite, .cite-card .copy-cite, .cite-card .continue-btn { font:inherit; font-size:.9rem; padding:.25rem .8rem; border:1px solid var(--accent); border-radius:.3rem; background:#fff; color:var(--accent); cursor:pointer; }
#search .copy-cite:hover, .cite-card .copy-cite:hover, .cite-card .continue-btn:hover { background:var(--accent); color:#fff; }
.cite-card { margin:0 0 1.5rem; padding:.7rem .9rem; border-left:3px solid var(--accent); background:#fafafa; }
.cite-card .cite-text { font-family:Georgia,"Times New Roman",serif; font-size:.95rem; }
.cite-card .cite-actions { display:flex; flex-wrap:wrap; gap:.5rem; margin-top:.5rem; }
.cite-card .continue-btn[hidden] { display:none; }
.player-box .yt-under { display:block; background:var(--card); padding:.4rem 1.25rem; font-size:.9rem; }
#search .cite-text a, .cite-card .cite-text a { color:var(--fg); text-decoration:none; overflow-wrap:anywhere; }   /* the YouTube address in a citation is a link, in black like the rest of the citation */
#search .pagefind-ui__result-tags { display:none; }   /* the gray metadata pills (date, series, session, video id ...) are not needed under a result */
/* a red rule, with room above and below, before each following search result */
#search .pagefind-ui__result-nested + .pagefind-ui__result-nested { border-top:1px solid var(--accent); margin-top:1.5rem; padding-top:1.5rem; }
#search .pagefind-ui__result + .pagefind-ui__result { border-top:1px solid var(--accent); margin-top:1.8rem; padding-top:1.8rem; }
mark.hit { background:#ffef5c; color:inherit; padding:0 .1em; border-radius:.15em; }
body.home:not(.browsing) .reccount { display:none; }   /* "142 recordings" repeats the sentence above; the count shows once a category is chosen (all widths) */
/* ---- desktop (64rem and wider) ---- */
.browse-links { display:none; }
@media (min-width:64rem) {
  /* every page: one row, TED-style: title at the left, Browse next to it, search box at the right; it never stacks */
  header.site .wrap { flex-wrap:nowrap; align-items:center; gap:1.75rem; }
  header.site strong, header.site .browse, header.site .hright { flex:none; }
  header.site .hright { margin:0 0 0 auto; }
  header.site .hsearch input { width:19rem; }
  /* home page: like Google, the search box is the star, with the categories as links under it */
  body.home header.site { border-bottom:0; background:transparent; padding:0 1.25rem; }
  body.home header.site .wrap { flex-direction:column; align-items:center; gap:1.6rem; max-width:none; padding:0 0 1.6rem; }
  body.home header.site strong { font-size:3.1rem; line-height:1.15; text-align:center; }
  body.home header.site .browse { display:none; }
  body.home header.site .hright { order:2; margin:0; width:min(44rem, 100%); }
  body.home header.site .hsearch input { width:100%; height:3.7rem; font-size:1.2rem; padding-left:3.2rem; background-size:1.4rem; background-position:1.1rem center; }
  body.home .browse-links { order:3; display:flex; flex-wrap:wrap; justify-content:center; align-items:center; gap:.4rem .8rem; font-size:1.15rem; }
  body.home .browse-links .bsep { color:var(--fg); font-weight:700; }
  body.home .browse-links a { color:var(--accent); }
  body.home .browse-links a[aria-current="true"] { color:var(--fg); font-weight:700; }
  body.home main { max-width:44rem; width:100%; margin:0 auto; }
  /* like Google, the whole block sits in the vertical middle of the page until a search or a category makes it longer */
  body.home:not(.searching):not(.browsing) { min-height:100vh; display:flex; flex-direction:column; justify-content:center; padding-bottom:4vh; }
  body.home:not(.searching):not(.browsing) main { padding-top:0; padding-bottom:0; }
  body.home:not(.searching):not(.browsing) #search { margin:0; }
  body.home header.site { width:100%; }
  body.home.searching header.site .wrap, body.home.browsing header.site .wrap { padding-top:2.5rem; }

}
.sessions-group h3 { margin:.9rem 0 0; font-size:1.05rem; text-transform:uppercase; letter-spacing:.04em; text-align:center; }
"""

# The search filter dropdowns (Country, Speaker, Type, Year) and the phone "Filters" button are hidden for now;
# the category pills in the header still filter. Set "show_search_filters": true in data/site-config.json to bring them back.
# The category pills in the header are hidden for now (they stay in the markup). "show_type_pills": true in
# data/site-config.json brings them back.
HIDE_PILLS_CSS = "\nheader.site .topnav { display:none !important; }\n"
HIDE_FILTERS_CSS = "\n#search .pagefind-ui__filter-panel, #search .filters-toggle { display:none !important; }\n"

PAGE_TMPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Techspressionism Video Archive</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Kanit:ital,wght@1,700;1,800&family=Lato:ital,wght@0,400;0,700;1,400;1,700&family=Open+Sans:wght@400&display=swap" rel="stylesheet">
<link rel="stylesheet" href="style.css">
<script>document.documentElement.className+=" js"</script>
</head>
<body>
{header}
<div class="stickyheader" id="stickytitle">{sticky_header}</div>
<main class="watch-page">
<article data-pagefind-body>
<div class="layout">
<div class="side">
{player}
<h1 data-pagefind-meta="title:{meta_title}">{label} <span class="topic">{topic}</span></h1>
<p class="meta">
<span data-pagefind-filter="type:{type_cap}" data-pagefind-meta="type:{type_cap}">{type_cap}</span> &middot;
{date_word} <span data-pagefind-filter="year:{year}" data-pagefind-meta="date:{date_iso}">{recorded}</span>{moderator}{curator}<br>
<span class="linkline"><a href="{url}" data-pagefind-meta="youtube:{url}">Watch on YouTube</a>{site_page}</span>
<span data-pagefind-meta="video_id:{video_id}" hidden></span>
<span data-pagefind-meta="session:{number}" hidden></span>
<span data-pagefind-meta="series:{series}" hidden></span>
<span data-pagefind-meta="participants:{participants}" hidden></span>
<span data-pagefind-meta="topic:{topic_meta}" hidden></span>
</p>
{speakers}
{flags}
<button type="button" class="read-btn" id="read-btn" aria-expanded="false" aria-controls="transcript" data-pagefind-ignore>Read transcript</button>
</div>
<div class="right">
{watch_next}
<div class="transcript" id="transcript">
{segments}
</div>
</div>
</div>
</article>
<section class="cite" data-pagefind-ignore>
<h2>Cite this session</h2>
<blockquote id="citation">{citation}</blockquote>
<button type="button" onclick="navigator.clipboard.writeText(document.getElementById('citation').innerText).then(()=>{{this.textContent='Copied';setTimeout(()=>this.textContent='Copy citation',1500)}})">Copy citation</button>
</section>
</main>
{player_js}
</body>
</html>
"""


def build_citation(entry):
    title = e(entry.get("session_title") or "Untitled")
    series = e(series_name(entry))
    date = entry.get("date_recorded")
    when = f"{'published' if date_is_estimate(entry) else 'recorded'} {fmt_date(date)}" if date else ""
    if entry.get("interviewee"):
        head = f"{e(participants_line(entry))}, {series}"
        head += f", {when}." if when else "."
    elif when:
        head = f"{series}, &ldquo;{title},&rdquo; {when}."
    else:
        head = f"{series}, &ldquo;{title}.&rdquo;"
    return f'{head} <em>{BRAND}</em>. <span class="doi">[DOI pending Zenodo deposit]</span>'


PLAY_SVG = '<svg viewBox="0 0 12 14" aria-hidden="true"><path d="M1 0.5v13l10.5-6.5z"/></svg>'
PILL_SVG = ('<svg viewBox="0 0 12 14" aria-hidden="true"><path d="M1.6 1.6v10.8l9-5.4z" fill="none" '
            'stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>')


def pill_time(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def build_player(entry):
    """The video, inline. Nothing is fetched from YouTube until a visitor presses play or a
    timecode: until then the box shows the recording's promo graphic (the YouTube thumbnail,
    cached by Stage 1), so opening a page tells YouTube nothing about the visitor."""
    video_id = entry["video_id"]
    alt = e(f"Play {label(entry)} — {entry.get('session_title') or 'Untitled'}")
    cite_data = json.dumps({"series": series_name(entry), "topic": entry.get("session_title") or "Untitled",
                            "date": entry.get("date_recorded") or "", "youtube": entry["url"],
                            "participants": participants_line(entry)}, ensure_ascii=False)
    img = (f'<img src="thumbnails/{e(video_id)}.jpg" alt="" loading="lazy">'
           if (THUMBNAILS_SRC_DIR / f"{video_id}.jpg").exists() else "")
    return (f'<div class="player-box" id="player-box" data-video="{e(video_id)}" data-lead="{PILL_LEAD_IN:g}" data-cite="{e(cite_data)}" data-pagefind-ignore>'
            f'<div class="player-frame" id="player"><button type="button" class="poster" aria-label="{alt}">{img}'
            f'<span class="bigplay">{PLAY_SVG}</span></button></div></div>')


CITE_JS = """// ---- citation formats: Chicago (the default), MLA, APA, BibTeX, RIS. The choice is remembered in the browser. ----
var CITE_FORMATS = [["chicago", "Chicago"], ["mla", "MLA"], ["apa", "APA"], ["bibtex", "BibTeX"], ["ris", "RIS (Zotero, EndNote)"]];
var CITE_MONTHS_LONG = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
var CITE_MONTHS_MLA = ["", "Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."];
var CITE_NL = String.fromCharCode(10), CITE_BS = String.fromCharCode(92);
var citeMem = null;     // kept in the page too, so the choice holds all visit long even where the browser refuses storage
function citeStored() { if (citeMem) return citeMem; try { var v = localStorage.getItem("tvaCiteFormat"); if (v) return v; } catch (e) {} return "chicago"; }
function citeStore(v) { citeMem = v; try { localStorage.setItem("tvaCiteFormat", v); } catch (e) {} }
function citeEsc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
function citeHms(sec) { sec = Math.floor(sec || 0); var t = function (n) { return String(n).padStart(2, "0"); }; return t(Math.floor(sec / 3600)) + ":" + t(Math.floor(sec % 3600 / 60)) + ":" + t(sec % 60); }
function citeYMD(iso) { var p = (iso || "").split("-").map(Number); return { y: p[0] || 0, m: p[1] || 0, d: p[2] || 0 }; }
function citeDateLong(p) { if (!p.y) return ""; if (!p.m) return String(p.y); return CITE_MONTHS_LONG[p.m] + (p.d ? " " + p.d + ", " : " ") + p.y; }
function citeDateMla(p) { if (!p.y) return ""; if (!p.m) return String(p.y); return (p.d ? p.d + " " : "") + CITE_MONTHS_MLA[p.m] + " " + p.y; }
function citeToday() { var d = new Date(); return d.getDate() + " " + CITE_MONTHS_MLA[d.getMonth() + 1] + " " + d.getFullYear(); }
function citeIsPerson(n) { return n && !/^Unidentified/i.test(n) && n.indexOf(",") < 0 && n.indexOf(" and ") < 0 && n.indexOf("&") < 0; }
function citeApaName(n) {
  var t = n.trim().split(/ +/);
  if (!citeIsPerson(n) || t.length < 2) return n.trim();
  return t[t.length - 1] + ", " + t.slice(0, -1).map(function (x) { return x.charAt(0).toUpperCase() + "."; }).join(" ");
}
function citeBibEsc(s) { return String(s).replace(/&/g, CITE_BS + "&").replace(/%/g, CITE_BS + "%"); }
function citeFormats(i) {
  var title = (i.series || "Techspressionism") + ": " + (i.topic || "Untitled");
  var pub = "Techspressionism Video Archive", p = citeYMD(i.date), ts = citeHms(i.seconds), url = i.url || "", who = i.speaker || "Unidentified speaker";
  var mla = who + '. "' + title + '." ' + pub + ", " + (p.y ? citeDateMla(p) + ", " : "") + ts + ", " + url + ". Accessed " + citeToday() + ".";
  var apa = citeApaName(who) + " " + (p.y ? "(" + citeDateLong(p).replace(/^([A-Za-z]+) ([0-9]+), ([0-9]+)$/, "$3, $1 $2").replace(/^([A-Za-z]+) ([0-9]+)$/, "$2, $1") + ")" : "(n.d.)") +
    ". " + title + " [Video transcript excerpt, " + ts + "]. " + pub + ". " + url;
  var last = (who.trim().split(/ +/).pop() || "tva").toLowerCase().replace(/[^a-z]/g, "") || "tva";
  var bib = ["@misc{" + last + (p.y || "nd") + "t" + Math.floor(i.seconds || 0) + ",",
    "  author = {" + citeBibEsc(who) + "},", "  title = {" + citeBibEsc(title) + "},",
    "  howpublished = {" + pub + ", streaming video},"].concat(p.y ? ["  year = {" + p.y + "},"] : [], p.m ? ["  month = {" + CITE_MONTHS_LONG[p.m].slice(0, 3).toLowerCase() + "},"] : [],
    ["  note = {Transcript passage at " + ts + (p.y ? "; recorded " + citeDateLong(p) : "") + "},", "  url = {" + url + "}", "}"]).join(CITE_NL);
  var ris = ["TY  - VIDEO", "AU  - " + (citeIsPerson(who) ? (who.trim().split(/ +/).length > 1 ? who.trim().split(/ +/).pop() + ", " + who.trim().split(/ +/).slice(0, -1).join(" ") : who) : who),
    "TI  - " + title, "T2  - " + pub].concat(p.y ? ["PY  - " + p.y, "DA  - " + p.y + "/" + (p.m ? String(p.m).padStart(2, "0") : "") + "/" + (p.d ? String(p.d).padStart(2, "0") : "") + "/"] : [],
    ["N1  - Transcript passage at " + ts, "UR  - " + url, "ER  - "]).join(CITE_NL);
  return { chicago: i.chicago || who + ', "' + title + '," ' + pub + ", " + (p.y ? citeDateLong(p) : "n.d.") + ", streaming video, " + ts + ", " + url + ".", mla: mla, apa: apa, bibtex: bib, ris: ris };
}
function citeFormatSelect(current) {
  return '<div class="cite-format"><label>Citation Format: <select class="cite-format-select" aria-label="Citation Format">' +
    CITE_FORMATS.map(function (f) { return '<option value="' + f[0] + '"' + (f[0] === current ? " selected" : "") + ">" + f[1] + "</option>"; }).join("") + "</select></label></div>";
}
// a web address inside a citation becomes a link (Copy Citation still copies plain text)
function citeLinkify(escaped) { return escaped.replace(/(https?:[/][/][^ ,<]+?)(?=[.]?(?:[ ,]|$))/g, '<a href="$1" target="_blank" rel="noopener">$1</a>'); }
function citeRender(info, fmt) {
  var text = citeFormats(info)[fmt] || citeFormats(info).chicago, code = fmt === "bibtex" || fmt === "ris";
  return { text: text, html: code ? citeEsc(text) : citeLinkify(citeEsc(text)), code: code };
}
function citeApply(block) {
  var info; try { info = JSON.parse(block.getAttribute("data-cite")); } catch (e) { return; }
  var fmt = citeStored(), r = citeRender(info, fmt), ct = block.querySelector(".cite-text"), b = block.querySelector(".copy-cite"), s = block.querySelector(".cite-format-select");
  if (ct) { ct.innerHTML = r.html; ct.classList.toggle("cite-code", r.code); }
  if (b) b.setAttribute("data-citation", r.text);
  if (s) s.value = fmt;
}
document.addEventListener("change", function (ev) {
  var s = ev.target.closest && ev.target.closest(".cite-format-select");
  if (!s) return;
  citeStore(s.value);
  [].slice.call(document.querySelectorAll("[data-cite]")).forEach(citeApply);
});
"""


PLAYER_JS = """<script>
(function () {
  var layout = document.querySelector('.layout'), btn = document.getElementById('read-btn');
  var qs = new URLSearchParams(location.search);       // a link from a search result carries: play=1, at (seconds), to, hl (search words), cite
  var cited = qs.get('play') === '1' && qs.get('at') !== null;
  var citeAt = cited ? parseFloat(qs.get('at')) : 0, stopAt = qs.get('to') ? parseFloat(qs.get('to')) : null;
  var hitWords = (qs.get('hl') || '').split(',').filter(Boolean), citeText = qs.get('cite') || '';
  var citedMode = false, continueBtn = null;
  if (cited) layout.classList.add('from-search');      // arrived from a search result: no Read/Hide transcript button
  function reading(on, scroll) {
    layout.classList.toggle('reading', on);
    btn.setAttribute('aria-expanded', String(on));
    btn.textContent = on ? 'Hide transcript' : 'Read transcript';
    if (on && scroll) document.getElementById('transcript').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  var pbox = document.getElementById('player-box');
  function sizePlayer() { if (pbox) document.documentElement.style.setProperty('--player-h', pbox.offsetHeight + 'px'); }
  sizePlayer(); window.addEventListener('resize', sizePlayer);
  var bar = document.getElementById('stickytitle'), pageHeader = document.querySelector('header.site');
  function titleBar() {     // once the page header has scrolled away, a slim bar with the site title keeps the way home in reach
    if (!bar || !pageHeader) return;
    var on = pageHeader.getBoundingClientRect().bottom < 0;
    if (on !== bar.classList.contains('on')) {
      bar.classList.toggle('on', on);
      document.documentElement.style.setProperty('--title-h', on ? bar.offsetHeight + 'px' : '0px');
    }
  }
  titleBar(); window.addEventListener('scroll', titleBar, { passive: true }); window.addEventListener('resize', titleBar);
  if (window.ResizeObserver && pbox) new ResizeObserver(sizePlayer).observe(pbox);
  if (btn) {
    btn.addEventListener('click', function () { reading(true, true); if (typeof preload === 'function') preload(); });
    var autoplay = /[?&]play=1(&|$)/.test(location.search);
    function fromHash() {                    // a search result or shared link points at a moment: open the transcript there
      var id = location.hash.slice(1), el = id && document.getElementById(id);
      if (el && document.getElementById('transcript').contains(el)) {
        reading(true, false); el.scrollIntoView();
        if (autoplay) {                      // "watch" from a search result, if the browser allows the video to start
          autoplay = false;
          if (cited) setTimeout(startCited, 300);
          else {
            var para = el.closest('.para') || el.parentElement.querySelector('.para'), pill = para && para.querySelector('a.pill');
            if (pill) setTimeout(function () { pill.click(); }, 300);
          }
        }
      }
    }
    fromHash();
    window.addEventListener('hashchange', fromHash);
    var cur = document.querySelector('.watch-next li.cur'), list = cur && cur.parentElement;
    if (list && list.clientHeight) list.scrollTop = cur.offsetTop - list.clientHeight / 2;
  }
  function highlightHits(para) {           // only the search words are marked, not the whole paragraph
    if (!hitWords.length) return;
    var BS = String.fromCharCode(92), LN = BS + 'p{L}' + BS + 'p{N}', SPECIAL = '.*+?^${}()|[]' + BS;
    var alt = hitWords.map(function (h) { return h.split('').map(function (ch) { return SPECIAL.indexOf(ch) >= 0 ? BS + ch : ch; }).join(''); }).join('|');
    var re = new RegExp('(?<![' + LN + '])(' + alt + ')(?![' + LN + '])', 'giu');
    var tx = para.querySelector('.tx'), walker = document.createTreeWalker(tx, NodeFilter.SHOW_TEXT), nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (n) {
      var s = n.nodeValue, last = 0, m, frag = document.createDocumentFragment(), any = false;
      re.lastIndex = 0;
      while ((m = re.exec(s))) {
        any = true;
        frag.appendChild(document.createTextNode(s.slice(last, m.index)));
        var mk = document.createElement('mark'); mk.className = 'hit'; mk.textContent = m[0]; frag.appendChild(mk);
        last = m.index + m[0].length;
      }
      if (any) { frag.appendChild(document.createTextNode(s.slice(last))); n.parentNode.replaceChild(frag, n); }
    });
  }
  function startCited() {
    var seek = Math.max(0, citeAt - lead);
    var i0 = 0;
    for (var i = 0; i < starts.length; i++) if (starts[i] <= citeAt + 0.01) i0 = i;
    var i1 = i0;
    while (i1 + 1 < starts.length && (stopAt === null || starts[i1 + 1] < stopAt)) i1++;
    for (var k = i0; k <= i1; k++) highlightHits(paras[k]);
    if (citeText) {                        // the citation sits right under the cited text, to copy once the clip has been watched
      var card = document.createElement('div');
      card.className = 'cite-card';
      var pdata = {};
      try { pdata = JSON.parse(pbox.getAttribute('data-cite') || '{}'); } catch (e) { pdata = {}; }
      var cinfo = { speaker: citeText.split(', "')[0], series: pdata.series, topic: pdata.topic, date: pdata.date, seconds: citeAt,
                    url: (pdata.youtube || '') + '&t=' + Math.floor(citeAt) + 's', chicago: citeText };
      card.setAttribute('data-cite', JSON.stringify(cinfo));
      card.innerHTML = '<strong class="cite-head">Citation information:</strong> <span class="cite-text"></span>'
        + '<div class="cite-actions"><button type="button" class="copy-cite">Copy Citation</button>'
        + '<button type="button" class="continue-btn" hidden>Continue watching &#9654;</button></div>' + citeFormatSelect(citeStored());
      citeApply(card);
      continueBtn = card.querySelector('.continue-btn');
      paras[i1].parentNode.insertBefore(card, paras[i1].nextSibling);
    }
    var ytBase = (document.querySelector('.meta a[href*="youtube.com/watch"]') || {}).href;
    if (ytBase && pbox) {                  // "Watch on YouTube" sits under the video, at the same moment
      var yl = document.createElement('a');
      yl.className = 'yt-under'; yl.target = '_blank'; yl.rel = 'noopener';
      yl.href = ytBase + '&t=' + Math.max(0, Math.floor(citeAt - lead)) + 's';
      yl.textContent = 'Watch on YouTube \u2197';
      pbox.appendChild(yl);
    }
    citedMode = true;
    whenReady(function () { player.seekTo(seek, true); player.playVideo(); });
  }
  document.addEventListener('click', function (ev) {
    var c = ev.target.closest && ev.target.closest('.continue-btn');
    if (c) { stopAt = null; c.hidden = true; if (player) player.playVideo(); return; }
    var b = ev.target.closest && ev.target.closest('.copy-cite');
    if (b && navigator.clipboard) {
      ev.preventDefault();
      navigator.clipboard.writeText(b.dataset.citation).then(function () {
        var o = b.textContent; b.textContent = 'Copied'; setTimeout(function () { b.textContent = o; }, 1500);
      });
    }
  });
  var box = document.getElementById('player-box');
  if (!box) return;
  var vid = box.dataset.video, lead = parseFloat(box.dataset.lead) || 0;
  var paras = [].slice.call(document.querySelectorAll('.para[data-t]'));
  var starts = paras.map(function (p) { return parseFloat(p.dataset.t); });
  var player = null, ready = false, failed = false, queue = [], forced = null, active = -1, lastUser = 0;
  function whenReady(fn) { if (ready) fn(); else { queue.push(fn); load(); } }
  var loading = false;
  function preload() { if (box && !player) load(); }   // opening the transcript is the sign of intent: have the player ready before the first WATCH tap (a phone only starts a video inside the tap)
  function load() {
    if (player || failed || loading) return;
    loading = true;
    window.onYouTubeIframeAPIReady = function () {
      document.getElementById('player').innerHTML = '<div id="yt"></div>';
      player = new YT.Player('yt', {
        videoId: vid, width: '100%', height: '100%',
        playerVars: { rel: 0, playsinline: 1, modestbranding: 1 },
        events: {
          onReady: function () { ready = true; queue.splice(0).forEach(function (f) { f(); }); },
          onError: function () { failed = true; queue = []; }
        }
      });
    };
    var s = document.createElement('script');
    s.src = 'https://www.youtube.com/iframe_api';
    s.onerror = function () { failed = true; };
    document.head.appendChild(s);
  }
  // the Cite button on a paragraph: pause the video and show that paragraph's citation right under it
  function closeParaCite() {
    [].slice.call(document.querySelectorAll('.para-cite')).forEach(function (c) { c.parentNode.removeChild(c); });
    [].slice.call(document.querySelectorAll('.cite-btn[aria-expanded="true"]')).forEach(function (b) { b.setAttribute('aria-expanded', 'false'); });
  }
  document.addEventListener('click', function (ev) {
    var cb = ev.target.closest && ev.target.closest('.cite-btn'), x = ev.target.closest && ev.target.closest('.close-cite');
    if (x) { closeParaCite(); return; }
    if (!cb) return;
    var para = cb.closest('.para'), open = cb.getAttribute('aria-expanded') === 'true';
    closeParaCite();
    if (open || !para) return;
    var wasPlaying = !!(ready && player && player.getPlayerState && player.getPlayerState() === 1);
    if (wasPlaying) player.pauseVideo();
    var pdata = {}, seg = para.closest('.seg'), sp = seg && seg.querySelector('.seg-head .speaker'), speaker = sp ? sp.textContent.trim() : '';
    try { pdata = JSON.parse(box.getAttribute('data-cite') || '{}'); } catch (e) { pdata = {}; }
    if (!speaker || ['unattributed', 'transcript', 'discussion', 'announcements'].indexOf(speaker.toLowerCase()) >= 0) speaker = pdata.participants || 'Unidentified speaker';
    var at = parseFloat(para.dataset.t) || 0;
    var info = { speaker: speaker, series: pdata.series, topic: pdata.topic, date: pdata.date, seconds: at, url: (pdata.youtube || '') + '&t=' + Math.floor(at) + 's' };
    var card = document.createElement('div');
    card.className = 'cite-card para-cite';
    card.setAttribute('data-cite', JSON.stringify(info));
    card.innerHTML = '<strong class="cite-head">Citation information:</strong> <span class="cite-text"></span>'
      + '<div class="cite-actions"><button type="button" class="copy-cite">Copy Citation</button>'
      + '<button type="button" class="continue-btn"' + (wasPlaying ? '' : ' hidden') + '>Continue watching &#9654;</button>'
      + '<button type="button" class="close-cite">Close</button></div>' + citeFormatSelect(citeStored());
    citeApply(card);
    para.parentNode.insertBefore(card, para.nextSibling);
    cb.setAttribute('aria-expanded', 'true');
    if (card.scrollIntoView) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  });
  box.querySelector('.poster').addEventListener('click', function () { whenReady(function () { player.playVideo(); }); });
  document.addEventListener('click', function (ev) {
    var a = ev.target.closest && ev.target.closest('a.pill');
    if (!a || failed || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return;   // no player: the link opens YouTube
    ev.preventDefault();
    var para = a.closest('.para'), i = paras.indexOf(para), seek = parseFloat(a.dataset.seek);
    stopAt = null; citedMode = false; if (continueBtn) continueBtn.hidden = true;
    [].slice.call(document.querySelectorAll('.para-cite .continue-btn')).forEach(function (c) { c.hidden = true; });
    forced = i; mark(i);
    whenReady(function () { player.seekTo(seek, true); player.playVideo(); });
  });
  ['wheel', 'touchmove', 'keydown'].forEach(function (n) { window.addEventListener(n, function () { lastUser = Date.now(); }, { passive: true }); });
  function mark(i, follow) {
    if (citedMode || i === active) return;
    if (active >= 0) paras[active].classList.remove('active');
    active = i;
    if (i < 0) return;
    paras[i].classList.add('active');
    if (follow && Date.now() - lastUser > 4000) {
      var r = paras[i].getBoundingClientRect(), top = window.innerWidth < 1024 ? Math.max(box.getBoundingClientRect().bottom, btn ? btn.getBoundingClientRect().bottom : 0) : (bar && bar.classList.contains('on') ? bar.offsetHeight : 0);
      if (r.top < top + 40 || r.bottom > window.innerHeight - 40) paras[i].scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }
  setInterval(function () {
    if (!ready || !player.getCurrentTime) return;
    var t = player.getCurrentTime(), playing = player.getPlayerState() === 1;
    if (stopAt !== null && playing && t >= stopAt) {         // the cited text is over: stop, and offer to carry on
      player.pauseVideo(); stopAt = null;
      if (continueBtn) continueBtn.hidden = false;
      return;
    }
    if (forced !== null) {
      if (t >= starts[forced] || t < starts[forced] - lead - 2) forced = null; else return;   // hold the clicked paragraph through the lead-in
    }
    var lo = 0, hi = starts.length - 1, idx = -1;
    while (lo <= hi) { var mid = (lo + hi) >> 1; if (starts[mid] <= t + 0.25) { idx = mid; lo = mid + 1; } else hi = mid - 1; }
    if (playing || idx !== active) mark(idx, playing);
  }, 250);
})();
</script>"""


PLAYER_JS = PLAYER_JS.replace("<script>" + chr(10) + "(function () {", "<script>" + chr(10) + CITE_JS + chr(10) + "(function () {", 1)


def emphasize(escaped):
    """_Title_ markers from Stage 5 -> <em>. Runs on already-escaped text; a
    doubled underscore (the '[__]' blank-audio artifact) never matches."""
    return re.sub(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])", r"<em>\1</em>", escaped)


def build_watch_next(entry, siblings):
    """Desktop sidebar (TED's 'Watch next' spot): every recording of the same type, newest first.
    Replaced by the transcript when 'Read transcript' is pressed."""
    info = TYPES[entry.get("type", "salon")]
    rows = []
    for x in siblings:
        when = fmt_date(x.get("date_recorded"))
        when = f"published {when}" if date_is_estimate(x) else when
        current = x is entry
        li_class = ' class="cur"' if current else ""
        aria = ' aria-current="page"' if current else ""
        title = e(x.get("session_title") or "Untitled")
        small = SMALL_THUMBS_SRC_DIR / f"{x['video_id']}.jpg"
        thumb = (f'<img src="thumbnails-small/{e(x["video_id"])}.jpg" alt="" width="96" height="54" loading="lazy">'
                 if small.exists() else '<span class="nothumb"></span>')
        rows.append(
            f'<li{li_class}><a href="{slug(x)}.html"{aria}>{thumb}'
            f'<span class="txt"><span class="t"><span class="num">#{x["number"]}</span> {title}</span>'
            f'<span class="d">{e(when)}</span></span></a></li>')
    return f'<aside class="watch-next" data-pagefind-ignore><h2>All {e(info["plural"])}</h2><ul>' + "".join(rows) + "</ul></aside>"


def build_session_page(entry, siblings=()):
    number = entry["number"]
    video_id = entry["video_id"]
    url = entry["url"]
    stype = entry["type"]

    year = (entry.get("date_recorded") or "")[:4] or "unknown"
    speakers = entry.get("speakers", [])
    countries = sorted({s["country"] for s in speakers if s.get("country")})

    if speakers or countries:
        sp_items = "".join(
            f'<li data-pagefind-filter="speaker:{facet(s["name"])}">{e(s["name"])}'
            + (f' <span class="country">{e(s.get("location") or s.get("country"))}</span>'
               if (s.get("location") or s.get("country")) else "")
            + "</li>"
            for s in speakers
        )
        country_tags = "".join(
            f'<span data-pagefind-filter="country:{facet(c)}" hidden></span>' for c in countries
        )
        speakers_html = (f'<details class="people"><summary>Participants ({len(speakers)})</summary>'
                         f'<ul class="speakers">{sp_items}</ul>{country_tags}</details>')
    else:
        speakers_html = ""

    flags_html = ""
    shown_flags = [f for f in (entry.get("flags") or [])
                   # per-utterance Zoom speaker labels make a missing timestamp index moot
                   if not (f == "speaker_index_missing" and entry.get("transcript_source") == "zoom-transcript")]
    if shown_flags:
        flags_html = (
            '<p class="flags" data-pagefind-ignore>Known gaps in this recording\'s metadata: '
            + e(", ".join(shown_flags).replace("_", " ")) + ".</p>"
        )

    all_unattributed = not any(seg.get("speaker") for seg in entry["segments"])
    seg_html = []
    prev_speaker = object()
    used_ids = set()
    for seg in entry["segments"]:
        used_ids.add(f't{int(seg.get("start") or 0)}')
    times_p, times_s = [], []       # paragraphs [anchor id, speaker] and sentences [start time, text, paragraph index]
    for seg in entry["segments"]:
        start = int(seg.get("start") or 0)
        speaker = seg.get("speaker") or ("Transcript" if all_unattributed else "Unattributed")
        # paragraph breaks are real "\n\n" in the text -- browsers collapse raw whitespace inside a
        # single <p>, so each becomes its own block, with its own timecode pill (the time the
        # paragraph's first word is spoken). The pill's link leads in a few seconds; its label is exact.
        raw = (seg.get("text") or "").split("\n\n")
        starts = seg.get("para_starts") or []
        blocks = []
        for k, para in enumerate(raw):
            para = para.strip()
            if not para:
                continue
            t0 = starts[k] if k < len(starts) and starts[k] is not None else (start if k == 0 else None)
            if t0 is None:
                blocks.append(f"<div class=\"para\"><p><span class=\"tx\">{emphasize(e(para))}</span>{suggest_link(entry, start, para)}</p></div>")
                continue
            seek = round(max(0.0, t0 - PILL_LEAD_IN), 1)
            yt = f"{url}&t={int(seek)}s"
            # each paragraph after the first is a heading with an id, so search results can point at
            # the paragraph (exact time) rather than at the start of the whole turn; the hidden name
            # keeps the speaker in the result's title. The first paragraph shares the turn heading's id.
            pid = f"t{int(t0)}"
            if k == 0 or pid in used_ids:
                head_open, head_name, head_id = '<h3 class="para-time">', "", ""
            else:
                used_ids.add(pid)
                head_open, head_id = f'<h3 class="para-time" id="{pid}">', pid
                head_name = f'<span class="speaker vh">{e(speaker)}</span>'
            sentences = split_sentences(para)
            st = (seg.get("sentence_times") or [])
            st = st[k] if k < len(st) else []
            if len(st) != len(sentences):
                st = [round(t0, 2)] * len(sentences)      # never guess a finer time than the paragraph's own
            times_p.append([f"t{start}" if k == 0 else (head_id or None), speaker])
            times_s += [[tt, s_text, len(times_p) - 1] for (_a, _b, s_text), tt in zip(sentences, st)]
            blocks.append(
                f'<div class="para" data-t="{t0}">'
                f'{head_open}<a class="pill" href="{e(yt)}" data-seek="{seek:g}" title="Play the video from here" data-pagefind-ignore>{pill_time(t0)}</a> <button type="button" class="cite-btn" aria-expanded="false" title="Pause the video and show a citation for this passage" data-pagefind-ignore>Cite</button>{head_name}</h3>'
                f'<p><span class="tx">{emphasize(e(para))}</span>{suggest_link(entry, t0, para)}</p></div>')
        cont = speaker == prev_speaker          # same speaker carrying on: no repeated name
        prev_speaker = speaker
        # a passage whose speaker is not identified is not offered as a search result (a citation needs a speaker);
        # the page still shows it. data/site-config.json "search_unidentified_speakers": true brings them back.
        unidentified = not seg.get("speaker") or is_not_speaker(seg.get("speaker"))
        no_index = " data-pagefind-ignore" if unidentified and not SITE_CONFIG.get("search_unidentified_speakers") else ""
        person_href = person_link(seg.get("speaker") or "") if not unidentified else ""
        speaker_html = f'<a href="{person_href}">{e(speaker)}</a>' if person_href else e(speaker)
        seg_html.append(
            f'<section class="seg{" cont" if cont else ""}"{no_index}>'
            f'<h2 class="seg-head" id="t{start}"><span class="speaker">{speaker_html}</span></h2>'
            f'{"".join(blocks) or "<div class=para><p></p></div>"}'
            f'</section>'
        )

    TIMES[slug(entry)] = {"p": times_p, "s": times_s}
    moderator = f" &middot; moderated by {e(entry['moderator'])}" if entry.get("moderator") else ""
    if entry.get("interviewer"):
        moderator = f" &middot; interviewed by {e(entry['interviewer'])}"
    curator = f" &middot; curated by {e(entry['curator'])}" if entry.get("curator") else ""

    return PAGE_TMPL.format(
        title=e(f"{label(entry)} — {entry.get('session_title') or 'Untitled'}"),
        meta_title=e(f"{label(entry)} — {entry.get('session_title') or 'Untitled'}"),
        label=e(label(entry)),
        series=e(series_name(entry)),
        participants=e(participants_line(entry)),
        date_word="published" if date_is_estimate(entry) else "recorded",
        player=build_player(entry),
        watch_next=build_watch_next(entry, siblings) if siblings else "",
        header=build_header(NAV_CORPUS, TYPES[entry.get('type', 'salon')]['label']),
        sticky_header=build_header(NAV_CORPUS, TYPES[entry.get('type', 'salon')]['label'], sid='-sticky', strip=False),
        player_js=PLAYER_JS,
        type=e(stype),
        type_cap=e(TYPES[stype]["label"]),
        year=e(year),
        date_iso=e(entry.get("date_recorded") or ""),
        video_id=e(video_id),
        number=number,
        topic=e(entry.get("session_title") or ""),
        topic_meta=e(entry.get("session_title") or "Untitled"),
        recorded=e(fmt_date(entry.get("date_recorded"))),
        moderator=moderator,
        curator=curator,
        url=e(url),
        site_page=site_page_html(entry),
        speakers=speakers_html,
        flags=flags_html,
        segments="\n".join(seg_html),
        citation=build_citation(entry),
    )


INDEX_TMPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Techspressionism Video Archive</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Kanit:ital,wght@1,700;1,800&family=Lato:ital,wght@0,400;0,700;1,400;1,700&family=Open+Sans:wght@400&display=swap" rel="stylesheet">
<link rel="stylesheet" href="style.css">
<script>document.documentElement.className+=" js"</script>
<link href="pagefind/pagefind-ui.css" rel="stylesheet">
<script src="pagefind/pagefind-ui.js"></script>
</head>
<body class="home">
{header}
<main>
<div id="intro-block">
<p class="intro">The Techspressionism Video Archive (TVA) is a searchable, citable transcript archive of recorded video related to Techspressionism published from {first_year}&ndash;{latest_year}.
This is a research tool intended for scholars, historians, and anyone with an interest in Techspressionism.
<a href="about.html">More about the archive and how to cite it.</a></p>
<p class="intro">The archive includes transcripts of Techspressionist <a href="index.html?type=Salon">salons</a>, artist <a href="index.html?type=Interview">interviews</a>,
<a href="index.html?type=Roundtable">roundtable discussions</a>, and artist <a href="index.html?type=Presentation">presentations</a>.
<strong>Transcripts are machine-generated and contain errors</strong>: <strong>verify every quote against the recording before citing.</strong></p>
<p class="intro">Built in Python with Claude Code. As of {as_of}, {n_recordings} recordings have been processed, with a running total of {hours:,} hours transcribed.</p>
</div>
<p class="reccount"><span id="rec-count">{count_text}</span></p>
<script>
(function () {{   // the intro shows on the home page, and on a category page only the first time a visitor sees it
  var seen = false;
  try {{ seen = sessionStorage.getItem("tvaSeen") === "1"; }} catch (e) {{}}
  if (new URLSearchParams(location.search).get("type") && seen) document.getElementById("intro-block").hidden = true;
  else try {{ sessionStorage.setItem("tvaSeen", "1"); }} catch (e) {{}}
}})();
</script>
<div id="search"></div>
<script>
{cite_js}
const CITATION_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];

function chicagoDate(iso) {{
  // Chicago Manual of Style date order: Month Day, Year. "n.d." (no date)
  // is CMOS's own convention for a missing publication date. Partial
  // dates ("2023-03", "2000") stay partial.
  if (!iso) return "n.d.";
  const [y, m, d] = iso.split("-").map(Number);
  if (!m) return String(y);
  if (!d) return CITATION_MONTHS[m] + " " + y;
  return CITATION_MONTHS[m] + " " + d + ", " + y;
}}

function hhmmss(totalSeconds) {{
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  const ss = String(sec).padStart(2, "0");
  return h ? (h + ":" + mm + ":" + ss) : (mm + ":" + ss);
}}

function escapeHtml(s) {{
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}}

// Chicago-style citation for one quoted passage, built entirely from public
// session metadata -- no per-speaker first/last-name splitting (this corpus
// has ~460 speaker names in too many inconsistent formats -- handles,
// duo credits, non-Western orderings -- to invert reliably), so names stay
// in the natural order they're recorded in.
const PILL_SVG = {pill_svg_js};
const REC_COUNTS = {rec_counts_js};

// The result text is the WHOLE sentence containing the match (search hit words highlighted), found in the
// page text by the hit's word position: back to the previous sentence end, forward to the next one.
const SENTENCE_END = /[.?!\u2026]["'\u201d\u2019)\]]*$/;
const NOT_SENTENCE_END = new Set(["mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "etc.", "e.g.", "i.e.", "no.", "jr.", "sr.", "prof."]);
function endsSentence(word) {{
  return SENTENCE_END.test(word) && !NOT_SENTENCE_END.has(word.toLowerCase());
}}
function sentenceParts(result, sr) {{
  const locs = (sr.locations || []).slice().sort((a, b) => a - b);
  if (!locs.length || !result.content) return null;
  const words = result.content.split(/\s+/);
  const first = locs[0];
  if (first >= words.length) return null;
  let start = first, end = first;
  while (start > 0 && first - start < 60 && !endsSentence(words[start - 1])) start--;
  while (end < words.length - 1 && end - first < 80 && !endsSentence(words[end])) end++;
  const hitIdx = new Set(locs), out = [], hits = new Set();
  for (let i = start; i <= end; i++) {{
    const w = escapeHtml(words[i]);
    if (hitIdx.has(i)) {{ out.push("<mark>" + w + "</mark>"); hits.add(words[i].replace(/[^\p{{L}}\p{{N}}'’-]+/gu, "").toLowerCase()); }}
    else out.push(w);
  }}
  return {{
    html: (start > 0 && !endsSentence(words[start - 1]) ? "… " : "") + out.join(" ") + (end < words.length - 1 && !endsSentence(words[end]) ? " …" : ""),
    text: words.slice(start, end + 1).join(" "),
    hits: [...hits].filter(Boolean)
  }};
}}

// ---- citations: the matched sentence with the sentence before and after it -------------------------------------
const CITES = new Map();
let citeSeq = 0;

// the YouTube address at the end of a citation is a link (Copy Citation still copies plain text)
function linkifyCitation(citation) {{
  return escapeHtml(citation).replace(/(https?:[/][/][^ ]+?)([.]?)$/, '<a href="$1" target="_blank" rel="noopener">$1</a>$2');
}}

function citationBlock(c) {{
  const meta = c.result.meta || {{}};
  const info = citationInfo(c.result, c.sr, c.at);
  const citation = info.chicago;
  const fmt = citeStored(), shown = citeRender(info, fmt);
  const yt = meta.youtube;
  const page = c.sr.url.split("#")[0];
  const here = page + "?play=1&at=" + c.at.toFixed(2) + (c.to != null ? "&to=" + c.to.toFixed(2) : "")
    + "&hl=" + encodeURIComponent(c.hits.join(",")) + "&cite=" + encodeURIComponent(citation) + "#" + c.anchor;
  return '<div class="citation-info" data-cid="' + c.id + '" data-cite="' + escapeHtml(JSON.stringify(info)) + '"' + (c.done ? ' data-enhanced="1"' : "") + '>'
    + '<strong class="cite-head">Citation information:</strong> <span class="cite-text' + (shown.code ? " cite-code" : "") + '">' + shown.html + '</span>'
    + '<div class="cite-actions"><button type="button" class="copy-cite" data-citation="' + escapeHtml(shown.text) + '">Copy Citation</button>'
    + '<a class="pill pill-watch" href="' + escapeHtml(here) + '" title="Watch here: opens the transcript at this sentence and plays the clip"><span class="watch-word">WATCH</span>' + {pill_svg_js} + pillTime(c.at) + '</a>'
    + '</div>' + citeFormatSelect(fmt) + '</div>';
}}

const timesCache = new Map();
function loadTimes(page) {{
  const m = page.match(new RegExp("((?:salon|interview|roundtable|presentation)-[0-9]{{3}})(?:[.]html|/)?(?:[?#]|$)"));
  if (!m) return Promise.resolve(null);
  if (!timesCache.has(m[1])) {{
    timesCache.set(m[1], fetch(location.pathname.replace(/[^\/]*$/, "") + "times/" + m[1] + ".json")
      .then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }}
  return timesCache.get(m[1]);
}}

const normText = (s) => s.toLowerCase().replace(/[^\p{{L}}\p{{N}}]+/gu, "");
const plainItalics = (s) => s.replace(/(^|[\s(])_([^_\s][^_]*?)_(?=[\s.,;:!?)]|$)/g, "$1$2");
// (no backslashes below: this text passes through Python string formatting)
const BS = String.fromCharCode(92);
const LN = BS + "p{{L}}" + BS + "p{{N}}";
const RE_SPECIAL = ".*+?^${{}}()|[]" + BS;
const escapeRe = (s) => s.split("").map((ch) => (RE_SPECIAL.indexOf(ch) >= 0 ? BS + ch : ch)).join("");
function markHits(text, hits) {{
  const safe = escapeHtml(plainItalics(text));
  if (!hits.length) return safe;
  return safe.replace(new RegExp("(?<![" + LN + "])(" + hits.map(escapeRe).join("|") + ")(?![" + LN + "])", "giu"), "<mark>$1</mark>");
}}

async function enhanceCitations(root) {{
  for (const el of root.querySelectorAll(".citation-info:not([data-enhanced])")) {{
    const c = CITES.get(Number(el.dataset.cid));
    if (!c || c.busy) continue;
    c.busy = true;
    const data = await loadTimes(c.sr.url);
    if (!el.isConnected) {{ c.busy = false; continue; }}
    const pi = data ? data.p.findIndex((p) => p[0] === c.anchor) : -1;
    const target = normText(c.sentenceText || "");
    const list = data ? data.s : [];
    let gi = -1;
    if (pi >= 0 && target) gi = list.findIndex((s) => s[2] === pi && normText(s[1]) === target);
    if (gi < 0) {{ el.setAttribute("data-enhanced", "1"); c.busy = false; continue; }}   // no exact match: keep the paragraph-level result
    const speaker = data.p[pi][1];
    const same = (i) => list[i] && data.p[list[i][2]][1] === speaker;
    const prior = same(gi - 1) ? list[gi - 1] : null;
    const next = same(gi + 1) ? list[gi + 1] : null;
    const lastIdx = next ? gi + 1 : gi;
    const after = list[lastIdx + 1];
    c.at = (prior || list[gi])[0];
    c.to = after ? after[0] : list[lastIdx][0] + 8;
    if (prior) c.anchor = data.p[prior[2]][0] || c.anchor;
    c.done = true;
    const html = (prior ? markHits(prior[1], c.hits) + " " : "") + c.sentenceHtml + (next ? " " + markHits(next[1], c.hits) : "");
    el.parentElement.innerHTML = html + citationBlock(c);
    c.busy = false;
  }}
}}

// Search matches WHOLE WORDS. The search engine's default also matches word beginnings and word stems, so "hat" finds
// hate, hated, hatch, hatred ...; putting each word in quotes makes it exact ("hat" finds hat and hats). Words the
// visitor already put in quotes (a phrase) are left as typed.
function exactQuery(text) {{
  const v = text.trim();
  if (!v || v.indexOf('"') >= 0) return v;
  return v.split(/\s+/).map((w) => '"' + w + '"').join(" ");
}}

function pillTime(seconds) {{
  seconds = Math.floor(seconds);
  const h = Math.floor(seconds / 3600), m = Math.floor(seconds % 3600 / 60), s = seconds % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h ? h + ":" + two(m) + ":" + two(s) : two(m) + ":" + two(s);
}}

function buildCitation(result, sr, seconds) {{
  return citationInfo(result, sr, seconds).chicago;
}}

function citationInfo(result, sr, seconds) {{
  const meta = result.meta || {{}};
  const unlabeled = !sr.title || !sr.title.trim() || ["unattributed", "transcript", "discussion", "announcements"].includes(sr.title.trim().toLowerCase());
  const speaker = !unlabeled ? sr.title.trim() : (meta.participants || "Unidentified speaker");
  const videoTitle = (meta.series || "Techspressionism") + ": " + (meta.topic || "Untitled");
  const publisher = "Techspressionism Video Archive";
  const date = chicagoDate(meta.date);
  const timestamp = hhmmss(seconds);
  const url = (meta.youtube || "") + (meta.youtube ? "&t=" + Math.floor(seconds) + "s" : "");
  const chicago = speaker + ', "' + videoTitle + '," ' + publisher + ", " + date + ", streaming video, " + timestamp + ", " + url + ".";
  return {{ speaker: speaker, series: meta.series || "Techspressionism", topic: meta.topic || "Untitled", date: meta.date || "", seconds: seconds, url: url, chicago: chicago }};
}}

window.addEventListener('DOMContentLoaded', () => {{
  const ui = new PagefindUI({{
    element: "#search",
    // resolve the bundle relative to wherever index.html actually sits
    // (site root locally, project subpath on GitHub Pages)
    bundlePath: location.pathname.replace(/[^/]*$/, "") + "pagefind/",
    showSubResults: true,
    showImages: false,
    pageSize: 8,
    translations: {{ placeholder: "Search transcripts…", zero_results: "No matches for [SEARCH_TERM]" }},
    processResult: (result) => {{
      // Pagefind derives its own base URL from bundlePath, so result URLs
      // already resolve correctly under a project subpath. Add a direct
      // deep-link to the matching second of the video, plus a ready-to-paste
      // Chicago-style citation, on each sub-result.
      const yt = result.meta && result.meta.youtube;
      for (const sr of (result.sub_results || [])) {{
        sr.title = (sr.title || "").replace(/^\s*WATCH\s*/, "");      // the timecode button's word is not part of the speaker's name
        const m = (sr.url || "").match(/#(t\d+)/);
        if (!yt || !m) continue;
        const parts = sentenceParts(result, sr);      // the whole sentence that matched, not a fixed-length snippet
        const c = {{ id: ++citeSeq, result, sr, anchor: m[1], at: parseInt(m[1].slice(1), 10), to: null,
                    hits: parts ? parts.hits : [], sentenceHtml: parts ? parts.html : sr.excerpt, sentenceText: parts ? parts.text : "", done: false }};
        CITES.set(c.id, c);
        sr.excerpt = c.sentenceHtml + citationBlock(c);       // provisional; enhanceCitations() adds the sentences before and after
      }}
      return result;
    }},
  }});
  // header search boxes on transcript pages send visitors here as ?q=term;
  // ?type=Interview preselects a media type
  const params = new URLSearchParams(location.search);
  setType(params.get("type") || "");
  const q = params.get("q");
  if (q) ui.triggerSearch(exactQuery(q));

  // phones: the filter dropdowns (Country, Speaker, Type, Year) sit behind one "Filters" button so the results start higher
  const searchBox = document.getElementById("search");
  new MutationObserver(() => enhanceCitations(searchBox)).observe(searchBox, {{ childList: true, subtree: true }});
  function filterCount(panel) {{
    let n = 0;
    for (const block of panel.querySelectorAll(".pagefind-ui__filter-block")) {{
      const name = (block.querySelector("summary") || {{}}).textContent || "";
      if (name.trim().toLowerCase() === "type") continue;      // the category pills at the top already show this one
      n += block.querySelectorAll("input:checked").length;
    }}
    return n;
  }}
  function hideEmptyFilters(panel) {{      // a filter value with no results for this search is not worth showing (unless it is ticked, so it can be unticked)
    for (const block of panel.querySelectorAll(".pagefind-ui__filter-block")) {{
      let shown = 0;
      for (const v of block.querySelectorAll(".pagefind-ui__filter-value")) {{
        const label = v.querySelector("label");
        const empty = /\(0\)\s*$/.test(label ? label.textContent : "") && !(v.querySelector("input") || {{}}).checked;
        const want = empty ? "none" : "";
        if (v.style.display !== want) v.style.display = want;
        if (!empty) shown++;
      }}
      const want = shown ? "" : "none";
      if (block.style.display !== want) block.style.display = want;
    }}
  }}
  function syncFiltersButton() {{
    const panel = searchBox.querySelector(".pagefind-ui__filter-panel");
    if (!panel) return;
    hideEmptyFilters(panel);
    let btn = searchBox.querySelector(".filters-toggle");
    if (!btn) {{
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "filters-toggle";
      btn.setAttribute("aria-expanded", String(searchBox.classList.contains("filters-open")));
      btn.addEventListener("click", () => {{
        const open = searchBox.classList.toggle("filters-open");
        btn.setAttribute("aria-expanded", String(open));
        syncFiltersButton();
      }});
      panel.parentElement.insertBefore(btn, panel);
    }}
    const n = filterCount(panel);
    const label = "<span>Filters" + (n ? " (" + n + ")" : "") + "</span><span aria-hidden='true'>" + (searchBox.classList.contains("filters-open") ? "&#9650;" : "&#9660;") + "</span>";
    if (btn.dataset.label !== label) {{      // only when it changed: writing to the page re-triggers this observer
      btn.dataset.label = label;
      btn.innerHTML = label;
    }}
  }}
  new MutationObserver(syncFiltersButton).observe(searchBox, {{ childList: true, subtree: true }});
  searchBox.addEventListener("change", syncFiltersButton);
  syncFiltersButton();

  function choose(type) {{      // one path for the header pills and the Browse dropdown
    setType(type);
    history.replaceState(null, "", type ? "?type=" + encodeURIComponent(type) : location.pathname);
    document.getElementById("intro-block").hidden = !!type;   // already seen: choosing a category hides the intro, "All" brings it back
  }}
  document.getElementById("typebar").addEventListener("click", (ev) => {{
    const btn = ev.target.closest("a[data-type]");
    if (btn && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey) {{
      ev.preventDefault();
      choose(btn.dataset.type);
    }}
  }});
  const browseSel = document.getElementById("browse-select");
  browseSel.removeAttribute("onchange");                       // on other pages it opens the home page; here it swaps the list
  // Artists list: filter as you type (the list holds only people heard or named in the recordings)
  const artistBox = document.getElementById("artist-filter");
  function filterArtists() {{
    if (!artistBox) return;
    const q = artistBox.value.trim().toLowerCase();
    const az = document.getElementById("azbar"); if (az) az.hidden = !!q;      // the letters are for browsing, not for a filtered list
    for (const li of document.querySelectorAll("#artist-list li")) {{
      li.hidden = !!q && !li.dataset.name.includes(q);
    }}
  }}
  if (artistBox) {{ artistBox.addEventListener("input", filterArtists); filterArtists(); }}
  browseSel.addEventListener("change", (ev) => choose(ev.target.value));
  document.querySelector(".browse-links").addEventListener("click", (ev) => {{
    const a = ev.target.closest("a[data-type]");
    if (a && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey) {{ ev.preventDefault(); choose(a.dataset.type); }}
  }});

  // the header search box is the search box: it drives the results shown on this page
  const headerSearch = document.querySelector("header.site .hsearch");
  const headerInput = headerSearch.querySelector("input");
  headerInput.removeAttribute("required");
  headerSearch.addEventListener("submit", (ev) => ev.preventDefault());
  let searchTimer;
  headerInput.addEventListener("input", () => {{
    clearTimeout(searchTimer);
    document.body.classList.toggle("searching", !!headerInput.value.trim());
    searchTimer = setTimeout(() => ui.triggerSearch(exactQuery(headerInput.value)), 150);
  }});
  if (q) {{ headerInput.value = q; document.body.classList.add("searching"); }}

  function setType(type) {{
    for (const b of document.querySelectorAll("#typebar a[data-type]")) {{
      b.setAttribute("aria-current", String(b.dataset.type === type));
    }}
    for (const g of document.querySelectorAll(".sessions-group")) {{
      g.hidden = !type || g.dataset.type !== type;      // no list until a category is chosen
    }}
    document.getElementById("browse-select").value = type;
    for (const a of document.querySelectorAll(".browse-links a[data-type]")) a.setAttribute("aria-current", String(a.dataset.type === type));
    document.body.classList.toggle("browsing", !!type);
    document.getElementById("rec-count").textContent = REC_COUNTS[type] || REC_COUNTS[""];   // the count and years follow the selected category
    // search always covers every category; choosing one here only changes the list shown below
  }}
}});

// event delegation: result cards render/re-render as the user types, so a
// single document-level listener beats wiring one per (transient) button
document.addEventListener('click', (e) => {{
  const btn = e.target.closest('.copy-cite');
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  navigator.clipboard.writeText(btn.dataset.citation).then(() => {{
    const original = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(() => {{ btn.textContent = original; }}, 1500);
  }});
}});
</script>
{groups}
</main>
</body>
</html>
"""



def person_key(name):
    s = unicodedata.normalize("NFKD", re.sub(r"\s*\([^)]*\)", "", name or "")).encode("ascii", "ignore").decode().lower()
    s = re.split(r"\s*/\s*", s)[0]
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()


NO_ARTIST_PAGE = {"chatgpt"}        # a voice heard in a recording that is not a person with an artist page


def load_people(corpus):
    """People from data/people.json plus what the recordings say about them: where they speak (identified
    speaker only) and where others name them (whole full name, in a passage whose speaker is identified)."""
    global PEOPLE, PERSON_BY_NORM, ARTIST_COUNT
    path = ROOT / "data" / "people.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    PEOPLE = data["people"]
    exhibitions = data["exhibitions"]
    status = json.loads((ROOT / "data" / "link-status.json").read_text()) if (ROOT / "data" / "link-status.json").exists() else {}
    PERSON_BY_NORM.clear()
    for p in PEOPLE:
        p["exhibition_labels"] = exhibitions
        p["speaks"], p["mentions"], p["interviews"] = {}, [], []
        for n in [p["name"]] + p.get("aliases", []):
            k = person_key(n)
            if k and k not in PERSON_BY_NORM:
                PERSON_BY_NORM[k] = p
        for kind in list(p["links"]):               # links found broken or parked are left out (listed in review/broken-links.csv)
            p["links"][kind] = [u for u in p["links"][kind] if status.get(u, {}).get("status") not in ("broken", "parked")]
    # who has an interview video of their own (the person interviewed, not the interviewer)
    for ent in corpus:
        who = ent.get("interviewee") if ent.get("type") == "interview" else None
        p = PERSON_BY_NORM.get(person_key(who)) if who else None
        if p is not None:
            p["interviews"].append(ent)
    for p in PEOPLE:
        p["interviews"].sort(key=lambda en: (en.get("date_recorded") or "", en["number"]))
    # where people speak
    for ent in corpus:
        for seg in ent["segments"]:
            sp = seg.get("speaker")
            if not sp or is_not_speaker(sp):
                continue
            # a joint label ("A & B", "A / B and C") counts for each of the people it names (lib_speakers DUOS)
            for who in lib_speakers.DUOS.get(re.sub(r"\s+", " ", sp).strip().lower()) or [sp]:
                p = PERSON_BY_NORM.get(person_key(who))
                if p is None or person_key(who) in NO_ARTIST_PAGE:
                    continue
                key = (ent.get("type", "salon"), ent["number"])
                r = p["speaks"].setdefault(key, {"ent": ent, "turns": 0, "words": 0, "first": seg["start"]})
                r["turns"] += 1
                r["words"] += len(seg["text"].split())
                r["first"] = min(r["first"], seg["start"])
    # where others name them
    names = {}
    for p in PEOPLE:
        for n in [p["name"]] + p.get("aliases", []):
            if person_key(n) in NO_ARTIST_PAGE:
                continue
            if (len(n.split()) >= 2 and len(n) >= 6 and "/" not in n) or lib_voicehints.distinctive_handle(n):     # a full name, or a distinctive handle such as ScoJo
                names.setdefault(n.lower(), p)
    if names:
        rx = re.compile(r"(?<![\w])(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")(?![\w])", re.I)
        for ent in corpus:
            for seg in ent["segments"]:
                sp = seg.get("speaker")
                if not sp or is_not_speaker(sp):
                    continue
                st = seg.get("sentence_times") or []
                for k, para in enumerate(seg["text"].split("\n\n")):
                    if not rx.search(para):
                        continue
                    spans = split_sentences(para)
                    times = st[k] if k < len(st) and len(st[k]) == len(spans) else None
                    for i, (a, z, txt) in enumerate(spans):
                        for m in rx.finditer(txt):
                            p = names[m.group(1).lower()]
                            if PERSON_BY_NORM.get(person_key(sp)) is p:
                                continue
                            tm = (times[i] if times else (seg.get("para_starts") or [seg["start"]])[k] or seg["start"])
                            if not any(x[3] == tm and x[0] is ent for x in p["mentions"]):
                                p["mentions"].append((ent, sp, txt, tm))
    ARTIST_COUNT = sum(1 for p in PEOPLE if p["speaks"] or p["mentions"])
    for p in PEOPLE:
        p["heard"] = bool(p["speaks"] or p["mentions"])
    LISTED[:] = [p for p in PEOPLE if p["heard"]]


def person_link(name):
    p = PERSON_BY_NORM.get(person_key(name))
    return f"artist-{p['id']}.html" if p and p.get("heard") else ""


def yt_moment(ent, seconds):
    """A YouTube address at the second (one second early)."""
    return f"{ent['url']}&t={max(0, int(seconds) - 1)}s"


def watch_pill(href, seconds, label="WATCH"):
    return (f'<a class="pill pill-watch" href="{e(href)}" target="_blank" rel="noopener">'
            f'<span class="watch-word">{label}</span>{PILL_SVG}{pill_time(seconds)}</a>')


def build_person_page(p):
    labels = p["exhibition_labels"]
    def count_link(n, one, many, anchor):
        return f'<li><a href="#{anchor}">{n:,} {one if n == 1 else many}</a></li>'
    profile = p.get("ts_profile") if p.get("ts_profile") and link_ok(p["ts_profile"]) else ""
    # every profile on techspressionism.com is a page of the Southampton exhibition, so the link sits under that show
    exhibitions = dict(p["exhibitions"])
    if profile and "southampton" not in exhibitions:
        exhibitions["southampton"] = []
    facts = []
    if p["speaks"]:
        facts.append(count_link(len(p["speaks"]), "recording", "recordings", "recordings"))
        facts.append(count_link(sum(r["turns"] for r in p["speaks"].values()), "time speaking", "times speaking", "recordings"))
    if p["mentions"]:
        facts.append(count_link(len(p["mentions"]), "mention", "mentions", "mentions"))
    if exhibitions:
        facts.append(count_link(len(exhibitions), "exhibition", "exhibitions", "exhibitions"))
    facts_html = "".join(facts)
    links = []
    for k, en in enumerate(p["interviews"]):
        many = f" ({e(fmt_date(en.get('date_recorded')))})" if len(p["interviews"]) > 1 else ""
        links.append(f'<a href="{en.get("type", "interview")}-{int(en["number"]):03d}.html">Artist Interview{many} &#8599;</a>')   # the interview's page in this archive
    for kind, text in (("website", "Website"), ("instagram", "Instagram"), ("wikipedia", "Wikipedia"), ("nft", "NFT"), ("twitter", "Twitter")):
        for u in p["links"].get(kind, [])[:2]:
            links.append(f'<a href="{e(u)}" target="_blank" rel="noopener">{text} &#8599;</a>')
    parts = [f'<h1>{e(p["name"])}</h1>']
    if p.get("location"):
        parts.append(f'<p class="where">{e(p["location"])}</p>')
    if p.get("aliases"):
        parts.append(f'<p class="where">Also appears as {e(", ".join(p["aliases"]))}</p>')
    if links:
        parts.append(f'<p class="links">{"".join(links)}</p>')
    if facts_html:
        parts.append(f'<ul class="facts">{facts_html}</ul>')
    if exhibitions:
        rows = []
        for slug, credits in exhibitions.items():
            info = labels.get(slug, {})
            title = e(info.get("label", slug.title()))
            link = (f'<a href="{e(info.get("url", "#"))}" target="_blank" rel="noopener">{title}</a>'
                    if link_ok(info.get("url", "")) else title)
            sub = "".join(f'<span class="sub">{e(c)}</span><br>' for c in credits[:3])
            if slug == "southampton" and profile:
                sub += f'<span class="sub"><a href="{e(profile)}" target="_blank" rel="noopener">Profile on techspressionism.com &#8599;</a></span><br>'
            reel = [r for r in p["reels"] if r["exhibition"] == slug]
            pills = "".join(watch_pill(f"https://www.youtube.com/watch?v={r['video']}&t={max(0, r['t'] - 1)}s", r["t"], "REEL") for r in reel[:1])
            rows.append(f'<li class="rowitem"><div><strong>{link}</strong><br>{sub}</div>{pills}</li>')
        parts.append('<h2 id="exhibitions">Exhibitions and collaborations</h2><ul>' + "".join(rows) + '</ul>'
                     '<p class="note">From the exhibition pages on techspressionism.com. A REEL button plays the exhibition reel at this artist\'s entry.</p>')
    if p["speaks"]:
        items = sorted(p["speaks"].values(), key=lambda r: (r["ent"].get("date_recorded") or "", r["ent"]["number"]), reverse=True)
        def row(r):
            en = r["ent"]
            slug_ = f"{en.get('type', 'salon')}-{int(en['number']):03d}"
            title = en.get("session_title") or ""
            return (f'<li class="rowitem"><div><a href="{slug_}.html"><strong>{e(label(en))}</strong></a> &middot; {e(fmt_date(en.get("date_recorded")))}'
                    f'<br><span class="sub">{e(title)}{" &middot; " if title else ""}spoke {r["turns"]} time{"s" if r["turns"] != 1 else ""}</span></div>'
                    f'{watch_pill(yt_moment(en, r["first"]), r["first"])}</li>')
        first, rest = items[:10], items[10:]
        more = f'<details><summary>Show {len(rest)} more recordings</summary><ul>{"".join(row(r) for r in rest)}</ul></details>' if rest else ""
        parts.append(f'<h2 id="recordings">Speaking in the archive</h2><ul>{"".join(row(r) for r in first)}</ul>{more}'
                     '<p class="note">Newest first. WATCH opens the YouTube video at their first words in that recording.</p>')
    if p["mentions"]:
        ms = sorted(p["mentions"], key=lambda m: (m[0].get("date_recorded") or "", m[3]), reverse=True)
        name_rx = re.compile("|".join(re.escape(n) for n in sorted([p["name"]] + p.get("aliases", []), key=len, reverse=True)), re.I)
        def mrow(m):
            en, sp, txt, tm = m
            body = e(re.sub(name_rx, lambda x: "\x00" + x.group(0) + "\x01", txt)).replace("\x00", "<mark>").replace("\x01", "</mark>")
            return (f'<li class="rowitem"><div>&ldquo;{body}&rdquo;<br><span class="sub">{e(sp)} &middot; {e(label(en))} &middot; '
                    f'{e(fmt_date(en.get("date_recorded")))}</span></div>{watch_pill(yt_moment(en, tm), tm)}</li>')
        first, rest = ms[:8], ms[8:60]
        more = f'<details><summary>Show {len(rest)} more</summary><ul>{"".join(mrow(m) for m in rest)}</ul></details>' if rest else ""
        parts.append(f'<h2 id="mentions">Mentioned by others</h2><ul>{"".join(mrow(m) for m in first)}</ul>{more}'
                     f'<p class="note">Passages where a speaker names them in full ({len(ms)} in all, newest first). Only passages with an identified speaker are shown.</p>')
    if not (p["speaks"] or p["mentions"] or exhibitions):
        parts.append('<p class="note">Nothing from the recordings yet. The details above come from the artist index on techspressionism.com.</p>')
    body = "\n".join(parts)
    head = build_header(NAV_CORPUS, "Artist")
    return (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{e(p["name"])} · Techspressionism Video Archive</title>\n{FONT_LINKS}\n<link rel="stylesheet" href="style.css">\n'
            f'<script>document.documentElement.className+=" js"</script>\n</head>\n<body class="person-page">\n{head}\n<main class="person">\n{body}\n</main>\n</body>\n</html>\n')


PERSON_CSS = """
main.person { max-width:52rem; }
.person h1 { margin:.2rem 0 .1rem; font-size:2.2rem; }
.person .where { color:var(--muted); margin:0 0 .8rem; }
.person .links { display:flex; flex-wrap:wrap; gap:.4rem 1.4rem; margin:0 0 1.5rem; }
.person h2 { font-size:1.25rem; margin:2rem 0 .6rem; border-top:1px solid var(--accent); padding-top:1rem; }
.person ul.facts { margin:.4rem 0 0; }
.person ul.facts li { margin:.15rem 0; font-size:1.1rem; }
.person h2[id] { scroll-margin-top:1rem; }
.person ul { list-style:none; padding:0; margin:0; }
.rowitem { display:flex; gap:1rem; align-items:center; justify-content:space-between; padding:.7rem 0; border-bottom:1px solid var(--line); }
.rowitem .sub { color:var(--muted); font-size:.92rem; }
.rowitem .pill { flex:none; }
.person .note { color:var(--muted); font-size:.9rem; margin-top:.6rem; }
.cite-example { border-left:3px solid var(--accent); padding:.2rem 0 .2rem 1rem; }
.sitefoot { max-width:60rem; margin:2.5rem auto 1.5rem; padding:0 1.25rem; text-align:center; font-size:.85rem; color:var(--muted); }
.sitefoot a { color:var(--muted); }
.about ul { list-style:disc; padding-left:1.4rem; margin:.5rem 0; }
.about li { margin:.3rem 0; }
.person details summary { cursor:pointer; color:var(--accent); margin:.8rem 0 .2rem; }
.person mark { background:#ffef5c; color:inherit; padding:0 .1em; border-radius:.15em; }
@media (max-width:40rem) { .rowitem { flex-direction:column; align-items:flex-start; } }
/* the Artists list on the home page */
.artist-tools { margin:.6rem 0 1rem; display:flex; flex-wrap:wrap; gap:.6rem 1.2rem; align-items:center; }
.artist-tools input[type=search] { font:inherit; padding:.5rem .9rem; border:2px solid var(--accent); border-radius:0; min-width:14rem; flex:1 1 14rem; }
.artist-tools label { font-size:.95rem; }
.azbar { display:flex; flex-wrap:wrap; gap:.15rem .7rem; justify-content:center; margin:0 0 .8rem; font-weight:700; }
.azbar[hidden] { display:none; }
#artist-list li[hidden], ul.sessions li[hidden] { display:none !important; }     /* the row styles set display:flex, which would otherwise override the hidden attribute (the filter box did nothing) */
#artist-list li[id] { scroll-margin-top:5rem; }
"""


def build_index(corpus):
    groups, spans = [], {}
    type_labels = [(t, TYPES[t]) for t in TYPES if any(x.get("type", "salon") == t for x in corpus)]
    for t, info in type_labels:
        entries = sorted((x for x in corpus if x.get("type", "salon") == t), key=list_order)
        rows = []
        # date range of the category; year-only placeholder dates (e.g. "2000") are ignored, and
        # data/site-config.json "series_start_years" can set the year a series began (Salons: 2020)
        yrs = [int(x["date_recorded"][:4]) for x in entries if len(x.get("date_recorded") or "") >= 7]
        if yrs:
            first = min(yrs + [int(SITE_CONFIG.get("series_start_years", {}).get(info["label"], min(yrs)))])
            last = max(yrs)
            span = f" ({first}&ndash;{last})" if first != last else f" ({first})"
            spans[info["label"]] = (len(entries), first, last)
        else:
            span = ""
        for entry in entries:
            topic = entry.get("session_title") or "Untitled"
            by = f' <span class="d">interviewed by {e(entry["interviewer"])}</span>' if entry.get("interviewer") else ""
            when = fmt_date(entry.get("date_recorded"))
            when = f"published {when}" if date_is_estimate(entry) else when
            small = SMALL_THUMBS_SRC_DIR / f"{entry['video_id']}.jpg"
            thumb = (f'<img class="thumb" src="thumbnails-small/{e(entry["video_id"])}.jpg" alt="" width="96" height="54" loading="lazy">'
                     if small.exists() else "")
            rows.append(
                f'<li>{thumb}<span class="num">#{entry["number"]}</span>'
                f'<span class="body"><a href="{slug(entry)}.html">{e(topic)}</a>{by}'
                f'<span class="d">{e(when)}</span></span></li>'
            )
        groups.append(
            f'<section class="sessions-group" data-type="{e(info["label"])}">'
            f'<ul class="sessions">' + "\n".join(rows) + "</ul></section>")
    if ARTIST_COUNT:                       # the Artists list: everyone with a page, those heard or named in the recordings first-class
        rows, letters = [], []
        def initial(name):       # the list is in last-name order; A-Z jump links go to the first name under each letter
            c = unicodedata.normalize("NFKD", name.split()[-1]).encode("ascii", "ignore").decode()[:1].upper()
            return c if c.isalpha() else "#"
        for pp in sorted(LISTED, key=lambda x: (x["name"].split()[-1].lower(), x["name"].lower())):
            bits = []
            if pp["speaks"]:
                bits.append(f'{len(pp["speaks"])} recording{"s" if len(pp["speaks"]) != 1 else ""}')
            elif pp["mentions"]:
                bits.append("named in the recordings")
            sub = " &middot; ".join(x for x in [e(pp.get("location") or "")] + bits if x)
            ini = initial(pp["name"])
            anchor = ""
            if ini not in letters:
                letters.append(ini)
                anchor = f' id="az-{"other" if ini == "#" else ini}"'
            rows.append(f'<li{anchor} data-name="{e(pp["name"].lower())}"><span class="body">'
                        f'<a href="artist-{pp["id"]}.html">{e(pp["name"])}</a><span class="d">{sub}</span></span></li>')
        groups.append(
            '<section class="sessions-group" data-type="Artist">'
            '<div class="artist-tools"><input type="search" id="artist-filter" placeholder="Find an artist by last name&hellip;" aria-label="Find an artist by last name">'
            '</div>'
            '<nav class="azbar" id="azbar" aria-label="Jump to a letter">' + " ".join(
                f'<a href="#az-{"other" if x == "#" else x}">{x}</a>' for x in sorted(letters, key=lambda x: (x == "#", x))) + '</nav>'
            '<ul class="sessions" id="artist-list">' + "\n".join(rows) + "</ul></section>")
        spans["Artist"] = (ARTIST_COUNT, 0, 0)
    def count_text(n, first, last):
        years = f"{first}\u2013{last}" if first != last else str(first)
        return f"{n} recording{'' if n == 1 else 's'} \u00b7 {years}."
    rec_counts = {label: count_text(*v) for label, v in spans.items() if label != "Artist"}
    if ARTIST_COUNT:
        rec_counts["Artist"] = f"{ARTIST_COUNT} artists heard or named in the recordings."
    if any(k != "Artist" for k in spans):
        rec_counts[""] = count_text(len(corpus), min(v[1] for k, v in spans.items() if k != "Artist"), max(v[2] for k, v in spans.items() if k != "Artist"))
    else:
        rec_counts[""] = f"{len(corpus)} recordings."

    latest_year = max((int(x["date_recorded"][:4]) for x in corpus if x.get("date_recorded")), default=2020)
    first_year = min((v[1] for k, v in spans.items() if k != "Artist"), default=latest_year)      # start of the earliest series (data/site-config.json series_start_years can set it)
    hours = round(sum(x.get("duration_seconds") or 0 for x in corpus) / 3600)
    as_of = datetime.date.today().strftime("%B %Y")
    return INDEX_TMPL.format(
        latest_year=latest_year,
        first_year=first_year,
        hours=hours,
        as_of=as_of,
        n_recordings=len(corpus),
        count_text=e(rec_counts[""]),
        rec_counts_js=json.dumps(rec_counts),
        groups="\n".join(groups),
        watch_lead_in=WATCH_LEAD_IN,
        header=build_header(corpus, ""),
        pill_svg_js=json.dumps(PILL_SVG),
        cite_js=CITE_JS,
        pill_lead=f"{PILL_LEAD_IN:g}",
    )


# ---- search-engine and AI-discovery markup (see lib_seo.py) ---------------------------------------------------------

ORG_NAME = SITE_CONFIG.get("organization_name") or "Techspressionism"
ORG_URL = SITE_CONFIG.get("organization_url") or "https://techspressionism.com/"
SERIES_GROUP = {"salon": "Techspressionist Salons", "interview": "Techspressionist Artist Interview Series",
                "roundtable": "Techspressionism Roundtables", "presentation": "Presentations"}


def entry_heading(entry):
    series, title = series_name(entry), (entry.get("session_title") or "").strip()
    return f"{series}: {title}" if title and title != series else series


def entry_people(entry):
    """People who speak or are interviewed, in order, without duplicates."""
    names = []
    for n in ([entry.get("interviewee"), entry.get("interviewer")] if entry.get("interviewee")
              else [s["name"] for s in entry.get("speakers", [])]):
        if n and not is_not_speaker(n) and n not in names:
            names.append(n)
    return names


def entry_description(entry):
    date = entry.get("date_recorded")
    when = f", {'published' if date_is_estimate(entry) else 'recorded'} {fmt_date(date)}" if date else ""
    people = entry_people(entry)
    with_ = ""
    if people:
        with_ = " With " + ", ".join(people[:4]) + (" and others" if len(people) > 4 else "") + "."
    return lib_seo.clip_text(f"Searchable, timestamped transcript of {entry_heading(entry)}{when}.{with_} "
                             "Every passage links to the exact moment in the video.", 300)


def entry_clips(entry):
    """[(speaker, start, end)]: one per stretch of an identified speaker of two minutes or more (at most 40, in time order)."""
    segs = [s for s in entry["segments"] if s.get("start") is not None]
    runs = []
    for i, s in enumerate(segs):
        sp = s.get("speaker")
        if runs and runs[-1][0] == sp:
            continue
        runs.append((sp, int(s["start"])))
    duration = int(entry.get("duration_seconds") or 0)
    clips = []
    for k, (sp, start) in enumerate(runs):
        end = runs[k + 1][1] if k + 1 < len(runs) else duration
        if sp and not is_not_speaker(sp) and end - start >= 120:
            clips.append((sp, start, end))
    return sorted(sorted(clips, key=lambda c: c[1] - c[2])[:40], key=lambda c: c[1])


def transcript_md_href(entry):
    return f"transcripts/{slug(entry)}.md"


def seo_for_entry(entry):
    base_home = canonical_url("")
    name = entry_heading(entry)
    title = f"{name} (Transcript)"
    desc = entry_description(entry)
    page = canonical_url(f"{slug(entry)}.html")
    thumb = canonical_url(f"thumbnails/{entry['video_id']}.jpg") if (SITE_DIR / "thumbnails" / f"{entry['video_id']}.jpg").exists() or \
        (THUMBNAILS_SRC_DIR / f"{entry['video_id']}.jpg").exists() else ""
    people = entry_people(entry)
    ld = None
    if page:
        info = TYPES[entry.get("type", "salon")]
        ld = lib_seo.video_graph(
            base=base_home, brand=BRAND, org_name=ORG_NAME, org_url=ORG_URL, page_url=page, name=title, description=desc, thumb=thumb,
            video_id=entry["video_id"], upload_date=entry.get("date_published") or entry.get("date_recorded") or "",
            recorded=None if date_is_estimate(entry) else entry.get("date_recorded"), duration=entry.get("duration_seconds"),
            series_name=entry.get("series") or SERIES_GROUP[entry.get("type", "salon")],
            people=[(n, canonical_url(person_link(n)) if person_link(n) else "") for n in people],
            clips=[(f"{sp}", s, e_) for sp, s, e_ in entry_clips(entry)],
            trail=[(BRAND, base_home), (info["plural"], canonical_url(f"index.html?type={info['label']}")), (name, page)])
    meta = lib_seo.scholar_meta(title=name, authors=people, recorded=entry.get("date_recorded"), publisher=BRAND, url=page or clean_path(f"{slug(entry)}.html"),
                                source_url=entry["url"]) if page else []
    alt = [("text/markdown", canonical_url(transcript_md_href(entry)) or transcript_md_href(entry), f"{name}: transcript as Markdown")]
    return dict(title=f"{title} · {BRAND}", social_title=title, description=desc, url=page, image=thumb, og_type="video.other", jsonld=ld,
                meta=meta, alternates=alt, video_embed=f"https://www.youtube.com/embed/{entry['video_id']}")


def seo_for_person(p):
    page = canonical_url(f"artist-{p['id']}.html")
    n_rec, n_men = len(p["speaks"]), len(p["mentions"])
    bits = []
    if n_rec:
        bits.append(f"speaks in {n_rec} recording{'s' if n_rec != 1 else ''}")
    if n_men:
        bits.append(f"is named in {n_men} passage{'s' if n_men != 1 else ''}")
    if p["exhibitions"]:
        bits.append(f"appears in {len(p['exhibitions'])} exhibition{'s' if len(p['exhibitions']) != 1 else ''} on techspressionism.com")
    lead = p["name"] + (f" ({p['location']})" if p.get("location") else "")
    desc = lib_seo.clip_text(f"{lead} {' and '.join(bits) if bits else 'appears in the Techspressionism Video Archive'}. "
                             "Timestamped transcript passages with links to the video, and links to their website and social pages.", 300)
    same_as = [u for k in ("website", "instagram", "wikipedia", "twitter", "nft") for u in p["links"].get(k, [])]
    ld = None
    if page:
        base_home = canonical_url("")
        apps = [(entry_heading(r["ent"]), canonical_url(f"{slug(r['ent'])}.html")) for r in
                sorted(p["speaks"].values(), key=lambda r: r["ent"].get("date_recorded") or "", reverse=True)[:10]]
        ld = lib_seo.person_graph(base=base_home, brand=BRAND, org_name=ORG_NAME, org_url=ORG_URL, page_url=page, name=p["name"], description=desc,
                                  aliases=p.get("aliases") or [], same_as=same_as, appearances=apps,
                                  trail=[(BRAND, base_home), ("Artists", canonical_url("index.html?type=Artist")), (p["name"], page)])
    return dict(title=f"{p['name']}: recordings, mentions and links · {BRAND}", social_title=f"{p['name']} · {BRAND}", description=desc,
                url=page, image="", og_type="profile", jsonld=ld, meta=[], alternates=[], video_embed="")


def archive_stats(corpus):
    years = [int(x["date_recorded"][:4]) for x in corpus if len(x.get("date_recorded") or "") >= 7 and not date_is_estimate(x)]   # a year-only placeholder date is ignored
    first = min(years + [int(v) for v in SITE_CONFIG.get("series_start_years", {}).values()]) if years else 2020
    return {"n": len(corpus), "hours": round(sum(x.get("duration_seconds") or 0 for x in corpus) / 3600),
            "first": first, "last": max(years) if years else first,
            "by_type": {k: sum(1 for x in corpus if x.get("type", "salon") == k) for k in TYPES},
            "as_of": datetime.date.today().strftime("%B %Y")}


def archive_summary(st):
    return (f"A searchable, citable transcript archive of {st['n']} recorded Techspressionism salons, artist interviews, roundtables and "
            f"presentations ({st['hours']} hours, {st['first']}–{st['last']}). Every passage links to the exact moment in the YouTube video.")


def seo_for_home(corpus):
    st = archive_stats(corpus)
    desc = archive_summary(st)
    page = canonical_url("")
    ld = None
    if page:
        ld = lib_seo.home_graph(base=page, brand=BRAND, org_name=ORG_NAME, org_url=ORG_URL, description=desc, first_year=st["first"],
                                last_year=st["last"], csv_url=canonical_url("data/recordings.csv"), license_url=SITE_CONFIG.get("license_url") or "",
                                doi=SITE_CONFIG.get("zenodo_doi") or "", youtube_channel=SITE_CONFIG.get("youtube_channel_url") or "")
    return dict(title=f"{BRAND} (TVA): searchable, citable transcripts of Techspressionism recordings", social_title=BRAND, description=desc,
                url=page, image="", og_type="website", jsonld=ld, meta=[], alternates=[("text/plain", canonical_url("llms.txt") or "llms.txt", "llms.txt")],
                video_embed="")


FOOTER = ('<footer class="sitefoot" data-pagefind-ignore><a href="about.html">About the archive and how to cite it</a> &middot; '
          '<a href="data/recordings.csv">Recordings (CSV)</a> &middot; <a href="llms.txt">llms.txt</a></footer>')


def add_seo(page_html, filename, seo):
    """Replace the <title>, add the description / social / JSON-LD tags before </head>, and the small footer before </body>."""
    page_html = re.sub(r"<title>.*?</title>", lambda m: f"<title>{e(seo['title'])}</title>", page_html, count=1, flags=re.S)
    tags = lib_seo.head_tags(title=seo["social_title"], description=seo["description"], url=seo["url"], image=seo["image"],
                             og_type=seo["og_type"], site_name=BRAND, jsonld=seo["jsonld"], meta=seo["meta"],
                             alternates=seo["alternates"], video_embed=seo["video_embed"])
    page_html = page_html.replace("</head>", tags + "\n</head>", 1)
    return page_html.replace("</body>", FOOTER + "\n</body>", 1)


def build_about(corpus):
    st = archive_stats(corpus)
    tn = st["by_type"]
    example = max((x for x in corpus if x.get("type", "salon") == "salon"), key=lambda x: x["number"])
    ex_title = entry_heading(example)
    ex_cite = (f'Speaker Name, &ldquo;{e(series_name(example))}: {e(example.get("session_title") or "Untitled")},&rdquo; {e(BRAND)}, '
               f'{e(fmt_date(example.get("date_recorded")))}, streaming video, 00:12:34, {e(example["url"])}&amp;t=754s.')
    page = canonical_url("about.html")
    desc = lib_seo.clip_text("What the Techspressionism Video Archive contains, how its transcripts are made and how accurate they are, "
                             "how to cite a passage, and where to download the data.", 300)
    body = f"""<h1>About the {e(BRAND)}</h1>
<p>The {e(BRAND)} (TVA) is a searchable, citable transcript archive of the recorded video published on the Techspressionism YouTube channel.
It holds {st['n']} recordings, {st['hours']} hours in all, made between {st['first']} and {st['last']}: {tn.get('salon', 0)} Techspressionist
<a href="index.html?type=Salon">salons</a>, {tn.get('interview', 0)} artist <a href="index.html?type=Interview">interviews</a>,
{tn.get('roundtable', 0)} <a href="index.html?type=Roundtable">roundtables</a> and {tn.get('presentation', 0)} <a href="index.html?type=Presentation">presentations</a>.
It is a research tool for scholars, historians, students and anyone interested in Techspressionism, the art and technology community
described at <a href="{e(ORG_URL)}" target="_blank" rel="noopener">techspressionism.com</a>. As of {st['as_of']}.</p>
<h2>What you can do here</h2>
<ul>
<li><strong>Search</strong> every recording at once from the home page. A search matches whole words and shows the sentence in context with a citation.</li>
<li><strong>Read a transcript</strong> beside its video. Every paragraph carries a timecode button that plays the video from that moment.</li>
<li><strong>Find an artist</strong> in the <a href="index.html?type=Artist">Artists</a> list: where they speak, where others name them, and links to their own pages.</li>
</ul>
<h2>How the transcripts are made</h2>
<p>Where Zoom produced a transcript it is used, because it labels who is speaking; otherwise the audio is transcribed with Whisper, an open
speech-recognition program, and where neither exists YouTube's captions are used. Timestamps are matched to the YouTube video, including for recordings
edited before upload. A speaker's name is shown only when it is supported by Zoom's own label, the speaker list in the video description, or a person who
has listened and confirmed it. Otherwise the passage is marked <em>Unattributed</em> and is not offered as a search result, because a passage
without a confirmed speaker cannot be cited.</p>
<p><strong>Transcripts are machine-generated and contain errors.</strong> Names, art terms and technical vocabulary are the most likely to be wrong.
Verify every quotation against the recording before citing it.</p>
<h2>How to cite</h2>
<p>Cite the passage with its speaker, the recording, the date, the timestamp and the YouTube address of the recording, which is permanent.
Every search result offers a ready-made citation with a Copy Citation button. The form is:</p>
<p class="cite-example">{ex_cite}</p>
<p>The example uses {e(ex_title)}; each recording page also shows its own citation line.</p>
<h2>Data and reuse</h2>
<ul>
<li><a href="data/recordings.csv">recordings.csv</a>: every recording with its title, dates, YouTube address and duration.</li>
<li>Each transcript as plain Markdown: <code>transcripts/&lt;recording&gt;.md</code>, for example <a href="{e(transcript_md_href(example))}">{e(slug(example))}.md</a>.
The recording pages link to theirs.</li>
<li><a href="llms.txt">llms.txt</a>: a Markdown index of the archive for AI assistants and search tools.{' <a href="sitemap.xml">sitemap.xml</a> lists every page.' if canonical_base() else ''}</li>
{f'<li>Permanent deposit with a DOI: <a href="https://doi.org/{e(SITE_CONFIG["zenodo_doi"])}">{e(SITE_CONFIG["zenodo_doi"])}</a>.</li>' if SITE_CONFIG.get("zenodo_doi") else ''}
</ul>
{f'<p>Reuse of the transcripts: <a href="{e(SITE_CONFIG["license_url"])}">{e(SITE_CONFIG.get("license_name") or "licence")}</a>.</p>' if SITE_CONFIG.get("license_url") else ''}
<p class="note">The archive is in beta and pages may change. Sources: the recordings on the
<a href="{e(SITE_CONFIG.get('youtube_channel_url') or 'https://www.youtube.com/@techspressionism')}" target="_blank" rel="noopener">Techspressionism YouTube channel</a>
and the exhibition and artist pages on techspressionism.com.</p>"""
    head = build_header(NAV_CORPUS, "")
    ld = lib_seo.about_graph(base=canonical_url(""), brand=BRAND, org_name=ORG_NAME, org_url=ORG_URL, page_url=page, description=desc,
                             trail=[(BRAND, canonical_url("")), ("About", page)]) if page else None
    html_page = (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                 f'<title>About the {e(BRAND)}</title>\n{FONT_LINKS}\n<link rel="stylesheet" href="style.css">\n'
                 f'<script>document.documentElement.className+=" js"</script>\n</head>\n<body class="person-page">\n{head}\n<main class="person about">\n{body}\n</main>\n</body>\n</html>\n')
    seo = dict(title=f"About the {BRAND}: coverage, method and how to cite", social_title=f"About the {BRAND}", description=desc, url=page, image="",
               og_type="website", jsonld=ld, meta=[], alternates=[], video_embed="")
    return add_seo(html_page, "about.html", seo)


def write_site_files(corpus):
    """Files for crawlers and AI tools: sitemap.xml, llms.txt, robots.txt, the Markdown transcripts and the recordings table."""
    (SITE_DIR / "transcripts").mkdir(exist_ok=True)
    (SITE_DIR / "data").mkdir(exist_ok=True)
    for old in (SITE_DIR / "transcripts").glob("*.md"):
        old.unlink()
    for entry in corpus:
        src = ROOT / "corpus" / f"{slug(entry)}.md"
        if src.exists():
            shutil.copy2(src, SITE_DIR / "transcripts" / src.name)
    csv_src = ROOT / "data" / "recordings.csv"
    if csv_src.exists():
        shutil.copy2(csv_src, SITE_DIR / "data" / "recordings.csv")
    base_home = canonical_url("")
    absu = lambda rel: canonical_url(rel) or clean_path(rel)
    st = archive_stats(corpus)
    groups = []
    for key, info in TYPES.items():
        items = []
        for x in sorted((c for c in corpus if c.get("type", "salon") == key), key=list_order):
            people = entry_people(x)
            note = "; ".join(bit for bit in [fmt_date(x.get("date_recorded")) if x.get("date_recorded") else "",
                                              ", ".join(people[:4]) + (" and others" if len(people) > 4 else "")] if bit)
            items.append((entry_heading(x), absu(transcript_md_href(x)), note))
        if items:
            groups.append((f"{info['plural']} ({len(items)})", items))
    (SITE_DIR / "llms.txt").write_text(lib_seo.llms_txt(
        brand=BRAND, summary=archive_summary(st), base=base_home, about_url=absu("about.html"), csv_url=absu("data/recordings.csv"),
        groups=groups, artists_url=absu("index.html?type=Artist"), sitemap_url=absu("sitemap.xml"), doi=SITE_CONFIG.get("zenodo_doi") or ""), encoding="utf-8")
    if base_home:
        pages = [{"loc": base_home}, {"loc": canonical_url("about.html")}]
        for x in corpus:
            thumb = canonical_url(f"thumbnails/{x['video_id']}.jpg")
            pages.append({"loc": canonical_url(f"{slug(x)}.html"), "video": {
                "thumb": thumb, "title": f"{entry_heading(x)} (Transcript)", "description": entry_description(x),
                "embed": f"https://www.youtube.com/embed/{x['video_id']}", "duration": x.get("duration_seconds") or 0,
                "published": x.get("date_published") or x.get("date_recorded") or ""}})
        pages += [{"loc": canonical_url(f"artist-{pp['id']}.html")} for pp in LISTED]
        (SITE_DIR / "sitemap.xml").write_text(lib_seo.sitemap_xml(pages), encoding="utf-8")
        if not noindex():                              # robots.txt only once the archive is meant to be found
            (SITE_DIR / "robots.txt").write_text(lib_seo.robots_txt(canonical_url("sitemap.xml"), SITE_CONFIG.get("allow_ai_training_crawlers", True)), encoding="utf-8")
        else:
            (SITE_DIR / "robots.txt").unlink(missing_ok=True)
    else:
        (SITE_DIR / "sitemap.xml").unlink(missing_ok=True)
        (SITE_DIR / "robots.txt").unlink(missing_ok=True)




def main():
    no_index = "--no-index" in sys.argv
    with open(CORPUS_JSON) as f:
        corpus = json.load(f)

    NAV_CORPUS[:] = corpus
    load_people(corpus)
    SITE_DIR.mkdir(exist_ok=True)
    global CSS_VERSION
    css_text = STYLE + PERSON_CSS + WP_MENU_CSS + ("" if SITE_CONFIG.get("show_search_filters") else HIDE_FILTERS_CSS) + ("" if SITE_CONFIG.get("show_type_pills") else HIDE_PILLS_CSS)
    CSS_VERSION = hashlib.md5(css_text.encode()).hexdigest()[:8]
    (SITE_DIR / "style.css").write_text(STYLE + PERSON_CSS + WP_MENU_CSS + ("" if SITE_CONFIG.get("show_search_filters") else HIDE_FILTERS_CSS) + ("" if SITE_CONFIG.get("show_type_pills") else HIDE_PILLS_CSS))
    for old in SITE_DIR.glob("*.html"):                     # the old .html addresses are gone
        if old.name != "index.html":
            old.unlink()
    for folder in [d for d in SITE_DIR.iterdir() if d.is_dir() and re.match(r"^(?:(?:salon|interview|roundtable|presentation)-[0-9]{3}|about|artist)$", d.name)]:
        shutil.rmtree(folder)
    write_page("", add_seo(add_robots(build_index(corpus), "index.html"), "index.html", seo_for_home(corpus)), 0)
    write_page("about", add_robots(build_about(corpus), "about.html"), 1)
    by_type = {}
    for entry in sorted(corpus, key=list_order):      # newest first, as on the home page
        by_type.setdefault(entry.get("type", "salon"), []).append(entry)
    for entry in corpus:
        write_page(slug(entry), add_seo(add_robots(build_session_page(entry, by_type[entry.get('type', 'salon')]), f"{slug(entry)}.html"),
                                        f"{slug(entry)}.html", seo_for_entry(entry)), 1)

    for pp in LISTED:                                   # only artists heard or named in the recordings have a page
        write_page(f"artist/{pp['id']}", add_seo(add_robots(build_person_page(pp), f"artist-{pp['id']}.html"),
                                                 f"artist-{pp['id']}.html", seo_for_person(pp)), 2)
    write_site_files(corpus)
    print(f"{len(LISTED)} artist pages (people heard or named in the recordings; {len(PEOPLE)} in the directory)")
    (SITE_DIR / "times").mkdir(exist_ok=True)
    for sl, data in TIMES.items():
        (SITE_DIR / "times" / f"{sl}.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    THUMBNAILS_OUT_DIR.mkdir(exist_ok=True)
    SMALL_THUMBS_OUT_DIR.mkdir(exist_ok=True)
    for src in SMALL_THUMBS_SRC_DIR.glob("*.jpg"):
        shutil.copy2(src, SMALL_THUMBS_OUT_DIR / src.name)
    copied = 0
    for entry in corpus:
        src = THUMBNAILS_SRC_DIR / f"{entry['video_id']}.jpg"
        if src.exists():
            shutil.copy2(src, THUMBNAILS_OUT_DIR / src.name)
            copied += 1
    print(f"Wrote {len(corpus) + 2} files to {SITE_DIR}, copied {copied} promo images")

    if no_index:
        print("Skipped Pagefind indexing (--no-index). Run: npx -y pagefind --site site")
        return

    print("Running Pagefind...")
    result = subprocess.run(
        ["npx", "-y", "pagefind", "--site", str(SITE_DIR)],
        capture_output=True, text=True,
    )
    print(result.stdout[-2000:])
    if result.returncode != 0:
        print(result.stderr[-2000:], file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
