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
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_media import TYPES, label, slug  # noqa: E402

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
PILL_LEAD_IN = max(0.0, float(SITE_CONFIG.get("pill_lead_in_seconds", 1)))


# Each page links back to the recording's own page on techspressionism.com (data/site-pages.json).
_SITE_PAGES_PATH = ROOT / "data" / "site-pages.json"
SITE_PAGES = json.loads(_SITE_PAGES_PATH.read_text()).get("pages", {}) if _SITE_PAGES_PATH.exists() else {}


def site_page_html(entry):
    address = SITE_PAGES.get(slug(entry))
    return (f' &middot; <a href="{e(address)}" target="_blank" rel="noopener" data-pagefind-ignore>'
            f'View on techspressionism.com</a>') if address else ""


def canonical_url(filename):
    base = (SITE_CONFIG.get("canonical_base") or "").strip().rstrip("/")
    return f"{base}/{filename}" if base else ""


def add_robots(page_html, filename=""):
    """Head tags that steer search engines. While the archive is in beta (data/site-config.json:
    "beta_noindex": true) every page asks not to be listed; it stays fully usable for anyone with the
    address. Set to false at launch. When "canonical_base" is set (the archive's final public address,
    e.g. https://techspressionism.com/archive/) every page also names its own canonical address, so a
    second copy of the site (such as the GitHub one) is never mistaken for the original."""
    tags = []
    if SITE_CONFIG.get("beta_noindex"):
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


def build_browse(corpus, active="", navigate=False):
    """'Browse' + a dropdown of the categories with counts. On the home page it swaps the list in place
    (script); on every other page (navigate=True) choosing one opens the home page on that category."""
    opts = "".join(
        f'<option value="{e(info["label"])}"{" selected" if info["label"] == active else ""}>{e(info["plural"])} ({n})</option>'
        for info, n in ((TYPES[k], sum(1 for x in corpus if x.get("type", "salon") == k)) for k in TYPES) if n)
    go = ' onchange="location.href=\'index.html\'+(this.value?\'?type=\'+encodeURIComponent(this.value):\'\')"' if navigate else ""
    return (f'<div class="browse"><label for="browse-select">Browse</label>'
            f'<select id="browse-select"{go}><option value="">Choose a category&hellip;</option>{opts}</select></div>')


def build_header(corpus, active=""):
    """The site header: title, [BETA], type pills, search box, Browse. The SAME markup on every page, so
    it always looks the same. (On the home page a script wires the search box and Browse to the page.)"""
    return ('<header class="site"><div class="wrap"><strong><a href="index.html">Techspressionism Video Archive</a> '
            '<span class="beta">[BETA]</span></strong>\n'
            + build_topnav(corpus, active) + '\n'
            '<div class="hright"><form class="hsearch" action="index.html" method="get" role="search">'
            '<input type="search" name="q" placeholder="Search transcripts&hellip;" aria-label="Search transcripts" required></form>\n'
            + build_browse(corpus, active, navigate=True) + '</div></div></header>')


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
@media (max-width:40rem) {   /* phones: the title fills the width of the screen on one line (17.85 = title + [BETA] length in em, plus a little slack) */
  header.site .wrap { row-gap:.1rem; }
  header.site strong { line-height:1.1; }
  header.site .topnav { flex:1 1 100%; justify-content:center; margin-top:.4rem; }   /* the pills fill the width, centred, in rows */
  header.site .topnav a.chip { flex:1 1 auto; text-align:center; }
  header.site .wrap > .d { display:none; }
  header.site strong { display:block; flex:1 1 100%; white-space:nowrap; font-size:6.4vw; font-size:calc(100cqw / 17.85); line-height:1.2; }
}
.topnav { display:flex; flex-wrap:wrap; gap:.4rem; align-items:center; }
.topnav a.chip { font-size:.9rem; line-height:1.4; padding:.25rem .8rem; border:1px solid var(--line); background:var(--card); border-radius:1rem; color:var(--fg); }
.topnav a.chip:hover { border-color:var(--accent); color:var(--accent); }
.topnav a.chip[aria-current="true"] { background:var(--accent); border-color:var(--accent); color:#fff; }
.topnav .n { opacity:.7; font-size:.8em; margin-left:.3rem; }
header.site .hright { margin:0 0 0 auto; display:flex; flex-direction:column; gap:.4rem; }
header.site .hsearch { margin:0; }
header.site .browse { margin:0; gap:.6rem; }
header.site .browse label { font-size:.9rem; }
header.site .browse select { padding:.3rem .7rem; font-size:.9rem; border-radius:1rem; background:var(--bg); }
header.site .hsearch input { font:inherit; font-weight:700; width:18rem; max-width:100%; height:2.9rem; padding:.4rem 1rem .4rem 2.8rem; border:2px solid var(--accent); border-radius:0; color:var(--fg);
  background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23666' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='10.5' cy='10.5' r='6.5'/%3E%3Cpath d='M15.5 15.5 21 21'/%3E%3C/svg%3E") no-repeat 1rem center / 1.2rem; }
header.site .hsearch input::placeholder { color:#757575; opacity:1; }
header.site .hsearch input::-webkit-search-cancel-button { cursor:pointer; }
header.site .hsearch input:focus { outline:none; border-color:var(--accent); }
@media (max-width:34rem) { header.site .hright { flex:1 1 100%; margin:.7rem 0 0; } header.site .hsearch input { width:100%; } }
main { max-width:60rem; margin:0 auto; padding:1.5rem 1.25rem 4rem; }
h1 { font-size:1.7rem; margin:.2rem 0 .3rem; }
h1 .topic { color:var(--muted); font-weight:400; }
.meta { color:var(--muted); margin:.2rem 0 1.2rem; }
.speakers { list-style:none; padding:0; margin:0 0 1.5rem; display:flex; flex-wrap:wrap; gap:.4rem .8rem; }
.speakers li { background:var(--card); border:1px solid var(--line); border-radius:1rem; padding:.15rem .7rem; font-size:.9rem; }
.speakers .country { color:var(--muted); }
.flags { background:#fff8e1; border:1px solid #ffe08a; border-radius:.4rem; padding:.5rem .8rem; font-size:.88rem; color:#7a5c00; margin-bottom:1.5rem; }
section.seg { padding:.9rem 0; border-top:1px solid var(--line); }
.seg-head { display:flex; align-items:baseline; gap:.7rem; margin:0 0 .7rem; font-size:1rem; scroll-margin-top:calc(var(--player-h, 56.25vw) + 4.6rem); }
.seg-head .speaker { font-weight:inherit; }
.read-btn { display:none; width:100%; margin:0 0 1.2rem; padding:.85rem 1rem; border:2px solid var(--accent); border-radius:.4rem; background:var(--accent);
            color:#fff; font:inherit; font-size:1.05rem; font-weight:800; letter-spacing:.08em; text-transform:uppercase; cursor:pointer; }
.read-btn:hover { background:#d60000; border-color:#d60000; }
.read-btn[aria-expanded="true"] { background:#fff; color:var(--accent); }
.read-btn[aria-expanded="true"]:hover { background:#fff0f0; }
.js .read-btn { display:block; position:sticky; top:var(--player-h, 56.25vw); z-index:15; box-shadow:0 .5rem 0 var(--bg); }   /* narrow: locks to the bottom edge of the pinned video */
.js .transcript { display:none; scroll-margin-top:calc(var(--player-h, 56.25vw) + 4.6rem); }
.js .layout.reading .transcript { display:block; }
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
.player-box { position:sticky; top:0; z-index:20; background:#000; margin:0 -1.25rem 1rem; }
.player-frame { position:relative; aspect-ratio:16/9; background:#000; }
.player-frame iframe, .player-frame img { position:absolute; inset:0; width:100%; height:100%; border:0; object-fit:cover; }
.player-frame .poster { position:absolute; inset:0; width:100%; height:100%; padding:0; border:0; background:#000; cursor:pointer; }
.player-frame .bigplay { position:absolute; left:50%; top:50%; width:4.2rem; height:4.2rem; margin:-2.1rem 0 0 -2.1rem; border-radius:50%; background:rgba(240,240,240,.92); display:flex; align-items:center; justify-content:center; transition:transform .15s; }
.player-frame .poster:hover .bigplay, .player-frame .poster:focus-visible .bigplay { transform:scale(1.08); }
.player-frame .bigplay svg { width:1.5rem; height:1.7rem; margin-left:.25rem; fill:#111; }
.para { margin:0 0 1.5rem; scroll-margin-top:calc(var(--player-h, 56.25vw) + 4.6rem); }
h3.para-time { margin:0; font-size:1rem; font-weight:400; line-height:1.4; scroll-margin-top:calc(var(--player-h, 56.25vw) + 4.6rem); }
.para p { margin:.6rem 0 0; font-size:1.05rem; line-height:1.65; }
a.pill { display:inline-flex; align-items:center; gap:.4rem; background:#f0f0f0; color:#333; border-radius:1.2rem; padding:.22rem .8rem .22rem .62rem; font-size:.92rem; line-height:1.4; font-variant-numeric:tabular-nums; }
a.pill:hover { background:#e7e7e7; text-decoration:none; }
a.pill svg { width:.72rem; height:.85rem; color:#8a8a8a; flex:none; }
a.pill:hover svg, a.pill:focus-visible svg { color:#FF0000; }
a.pill:hover svg path, a.pill:focus-visible svg path { fill:currentColor; }   /* solid red triangle on hover */
.para .tx { border-radius:.15rem; }
.para.active .tx { background:#fdebc8; -webkit-box-decoration-break:clone; box-decoration-break:clone; }
@media (min-width:64rem) {
  .layout { display:grid; grid-template-columns:minmax(0,1.7fr) minmax(24rem,1fr); gap:2.5rem; align-items:start; }
  .side { display:block; position:sticky; top:1rem; max-height:calc(100vh - 2rem); overflow:auto; scrollbar-width:thin; }
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
.sessions li { border-bottom:1px solid var(--line); padding:.7rem 0; }
.sessions .num { display:inline-block; min-width:3.2rem; color:var(--muted); font-variant-numeric:tabular-nums; }
.sessions .d { color:var(--muted); font-size:.9rem; }
#search { margin:.4rem 0 .3rem; }
.reccount { margin:.2rem 0 .6rem; color:var(--muted); }
#search .pagefind-ui__form { display:none; }   /* the header search box replaces the widget's own input */
.browse { display:flex; align-items:center; gap:.8rem; margin:.7rem 0 1rem; }
.browse[hidden] { display:none; }
.browse label { font:inherit; }   /* same font as the intro line */
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
.more-link { display:none; padding:0; border:0; background:none; font:inherit; color:var(--accent); cursor:pointer; }
.js .more-link { display:inline; }
.js .about-more:not(.open) { display:none; }
.intro { color:var(--fg); max-width:44rem; margin:.2rem 0 .6rem; }
.tagline { font-size:inherit; }   /* same size as the About heading; lines fill the width (no balanced wrapping) */
.intro .watch-ref { color:var(--accent); font-weight:600; }
.yt-jump { white-space:nowrap; font-size:.85em; }
.citation-info { margin-top:.5rem; padding:.5rem .7rem; background:var(--bg); border:1px solid var(--line); border-radius:.35rem; font-size:.85em; color:#333; }
.citation-info strong { display:block; margin-bottom:.2rem; color:var(--muted); font-size:.85em; font-weight:600; }
.citation-info .cite-text { font-family:Georgia,"Times New Roman",serif; }
.cite-actions { display:flex; flex-wrap:wrap; align-items:center; gap:.4rem .5rem; margin-top:.4rem; }
.cite-actions a.pill { font-size:.85rem; padding:.18rem .65rem .18rem .55rem; }
.citation-info .copy-cite { display:block; margin:0; font:inherit; font-size:.85em; padding:.2rem .6rem; border:1px solid var(--line); background:var(--card); border-radius:.3rem; cursor:pointer; }
.citation-info .copy-cite:hover { border-color:var(--accent); color:var(--accent); }
.cite { margin:2.5rem 0 0; padding:1rem 1.1rem; background:var(--card); border:1px solid var(--line); border-radius:.5rem; }
.cite h2 { font-size:.95rem; margin:0 0 .5rem; }
.cite blockquote { margin:0; font-size:.92rem; color:#333; }
.cite button { margin-top:.6rem; font:inherit; font-size:.82rem; padding:.25rem .7rem; border:1px solid var(--line); background:var(--bg); border-radius:.3rem; cursor:pointer; }
.cite button:hover { border-color:var(--accent); color:var(--accent); }
.cite .doi { color:var(--muted); }
.sessions-group h3 { margin:.9rem 0 0; font-size:1.05rem; text-transform:uppercase; letter-spacing:.04em; text-align:center; }
"""

# The search filter dropdowns (Country, Speaker, Type, Year) and the phone "Filters" button are hidden for now;
# the category pills in the header still filter. Set "show_search_filters": true in data/site-config.json to bring them back.
HIDE_FILTERS_CSS = "\n#search .pagefind-ui__filter-panel, #search .filters-toggle { display:none !important; }\n"

PAGE_TMPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Techspressionism Video Archive</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Kanit:ital,wght@1,700;1,800&family=Lato:ital,wght@0,400;0,700;1,400;1,700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="style.css">
<script>document.documentElement.className+=" js"</script>
</head>
<body>
{header}
<main class="watch-page">
<article data-pagefind-body>
<div class="layout">
<div class="side">
{player}
<h1 data-pagefind-meta="title:{meta_title}">{label} <span class="topic">{topic}</span></h1>
<p class="meta">
<span data-pagefind-filter="type:{type_cap}" data-pagefind-meta="type:{type_cap}">{type_cap}</span> &middot;
{date_word} <span data-pagefind-filter="year:{year}" data-pagefind-meta="date:{date_iso}">{recorded}</span>{moderator}{curator}<br>
<a href="{url}" data-pagefind-meta="youtube:{url}">Watch on YouTube</a>{site_page}
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
    img = (f'<img src="thumbnails/{e(video_id)}.jpg" alt="" loading="lazy">'
           if (THUMBNAILS_SRC_DIR / f"{video_id}.jpg").exists() else "")
    return (f'<div class="player-box" id="player-box" data-video="{e(video_id)}" data-lead="{PILL_LEAD_IN:g}" data-pagefind-ignore>'
            f'<div class="player-frame" id="player"><button type="button" class="poster" aria-label="{alt}">{img}'
            f'<span class="bigplay">{PLAY_SVG}</span></button></div></div>')


PLAYER_JS = """<script>
(function () {
  var layout = document.querySelector('.layout'), btn = document.getElementById('read-btn');
  function reading(on, scroll) {
    layout.classList.toggle('reading', on);
    btn.setAttribute('aria-expanded', String(on));
    btn.textContent = on ? 'Hide transcript' : 'Read transcript';
    if (on && scroll) document.getElementById('transcript').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  var pbox = document.getElementById('player-box');
  function sizePlayer() { if (pbox) document.documentElement.style.setProperty('--player-h', pbox.offsetHeight + 'px'); }
  sizePlayer(); window.addEventListener('resize', sizePlayer);
  if (window.ResizeObserver && pbox) new ResizeObserver(sizePlayer).observe(pbox);
  if (btn) {
    btn.addEventListener('click', function () { reading(!layout.classList.contains('reading'), true); });
    var autoplay = /[?&]play=1(&|$)/.test(location.search);
    function fromHash() {                    // a search result or shared link points at a moment: open the transcript there
      var id = location.hash.slice(1), el = id && document.getElementById(id);
      if (el && document.getElementById('transcript').contains(el)) {
        reading(true, false); el.scrollIntoView();
        if (autoplay) {                      // "watch" from a search result: start the video at that paragraph, if the browser allows
          autoplay = false;
          var para = el.closest('.para') || el.parentElement.querySelector('.para'), pill = para && para.querySelector('a.pill');
          if (pill) setTimeout(function () { pill.click(); }, 300);
        }
      }
    }
    fromHash();
    window.addEventListener('hashchange', fromHash);
    var cur = document.querySelector('.watch-next li.cur'), list = cur && cur.parentElement;
    if (list && list.clientHeight) list.scrollTop = cur.offsetTop - list.clientHeight / 2;
  }
  var box = document.getElementById('player-box');
  if (!box) return;
  var vid = box.dataset.video, lead = parseFloat(box.dataset.lead) || 0;
  var paras = [].slice.call(document.querySelectorAll('.para[data-t]'));
  var starts = paras.map(function (p) { return parseFloat(p.dataset.t); });
  var player = null, ready = false, failed = false, queue = [], forced = null, active = -1, lastUser = 0;
  function whenReady(fn) { if (ready) fn(); else { queue.push(fn); load(); } }
  function load() {
    if (player || failed) return;
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
  box.querySelector('.poster').addEventListener('click', function () { whenReady(function () { player.playVideo(); }); });
  document.addEventListener('click', function (ev) {
    var a = ev.target.closest && ev.target.closest('a.pill');
    if (!a || failed || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return;   // no player: the link opens YouTube
    ev.preventDefault();
    var para = a.closest('.para'), i = paras.indexOf(para), seek = parseFloat(a.dataset.seek);
    forced = i; mark(i);
    whenReady(function () { player.seekTo(seek, true); player.playVideo(); });
  });
  ['wheel', 'touchmove', 'keydown'].forEach(function (n) { window.addEventListener(n, function () { lastUser = Date.now(); }, { passive: true }); });
  function mark(i, follow) {
    if (i === active) return;
    if (active >= 0) paras[active].classList.remove('active');
    active = i;
    if (i < 0) return;
    paras[i].classList.add('active');
    if (follow && Date.now() - lastUser > 4000) {
      var r = paras[i].getBoundingClientRect(), top = window.innerWidth < 1024 ? Math.max(box.getBoundingClientRect().bottom, btn ? btn.getBoundingClientRect().bottom : 0) : 0;
      if (r.top < top + 40 || r.bottom > window.innerHeight - 40) paras[i].scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }
  setInterval(function () {
    if (!ready || !player.getCurrentTime) return;
    var t = player.getCurrentTime(), playing = player.getPlayerState() === 1;
    if (forced !== null) {
      if (t >= starts[forced] || t < starts[forced] - lead - 2) forced = null; else return;   // hold the clicked paragraph through the lead-in
    }
    var lo = 0, hi = starts.length - 1, idx = -1;
    while (lo <= hi) { var mid = (lo + hi) >> 1; if (starts[mid] <= t + 0.25) { idx = mid; lo = mid + 1; } else hi = mid - 1; }
    if (playing || idx !== active) mark(idx, playing);
  }, 250);
})();
</script>"""


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
            blocks.append(
                f'<div class="para" data-t="{t0}">'
                f'{head_open}<a class="pill" href="{e(yt)}" data-seek="{seek:g}" data-pagefind-ignore>{PILL_SVG}{pill_time(t0)}</a>{head_name}</h3>'
                f'<p><span class="tx">{emphasize(e(para))}</span>{suggest_link(entry, t0, para)}</p></div>')
        cont = speaker == prev_speaker          # same speaker carrying on: no repeated name
        prev_speaker = speaker
        seg_html.append(
            f'<section class="seg{" cont" if cont else ""}">'
            f'<h2 class="seg-head" id="t{start}"><span class="speaker">{e(speaker)}</span></h2>'
            f'{"".join(blocks) or "<div class=para><p></p></div>"}'
            f'</section>'
        )

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
<link href="https://fonts.googleapis.com/css2?family=Kanit:ital,wght@1,700;1,800&family=Lato:ital,wght@0,400;0,700;1,400;1,700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="style.css">
<script>document.documentElement.className+=" js"</script>
<link href="pagefind/pagefind-ui.css" rel="stylesheet">
<script src="pagefind/pagefind-ui.js"></script>
</head>
<body>
{header}
<main>
<div id="intro-block">
<p class="intro tagline">A searchable, citable transcript archive of recorded video from 2020&ndash;{latest_year}. <button type="button" class="more-link" id="about-toggle" aria-expanded="false" aria-controls="about-more">more...</button></p>
<div class="about-more" id="about-more">
<p class="intro">The Techspressionism Video Archive (TVA) is a tool for researchers, historians and anyone studying the Techspressionism movement:
a searchable, citable record of what was said in its recorded video. It contains the monthly <a href="index.html?type=Salon">Techspressionist Salons</a>
(running since September 2020), artist <a href="index.html?type=Interview">interviews</a>, <a href="index.html?type=Roundtable">roundtables</a>,
and <a href="index.html?type=Presentation">presentations</a>. Search the full text below &mdash; all recordings or just one type &mdash; or browse the list.
Every result links to the transcript and to the exact moment in the recording. Transcripts are machine-generated (Zoom, YouTube, and Whisper) and may
contain errors &mdash; always verify a quote against the recording (the <span class="watch-ref">&#9654;&nbsp;timecode</span> button) before citing.
Built in Python with Claude Code. {hours:,} hours transcribed and indexed.</p></div>
</div>
<p class="reccount"><span id="rec-count">{count_text}</span></p>
<script>
(function () {{   // the intro shows on the home page, and on a category page only the first time a visitor sees it
  var seen = false;
  try {{ seen = sessionStorage.getItem("tvaSeen") === "1"; }} catch (e) {{}}
  const tog = document.getElementById("about-toggle"), more = document.getElementById("about-more");
  tog.addEventListener("click", () => {{
    const open = more.classList.toggle("open");
    tog.setAttribute("aria-expanded", String(open));
    tog.textContent = open ? "less" : "more...";
  }});
  if (new URLSearchParams(location.search).get("type") && seen) document.getElementById("intro-block").hidden = true;
  else try {{ sessionStorage.setItem("tvaSeen", "1"); }} catch (e) {{}}
}})();
</script>
<div id="search"></div>
<script>
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
function sentenceExcerpt(result, sr) {{
  const locs = (sr.locations || []).slice().sort((a, b) => a - b);
  if (!locs.length || !result.content) return "";
  const words = result.content.split(/\s+/);
  const first = locs[0];
  if (first >= words.length) return "";
  let start = first, end = first;
  while (start > 0 && first - start < 60 && !endsSentence(words[start - 1])) start--;
  while (end < words.length - 1 && end - first < 80 && !endsSentence(words[end])) end++;
  const hits = new Set(locs);
  const out = [];
  for (let i = start; i <= end; i++) {{
    const w = escapeHtml(words[i]);
    out.push(hits.has(i) ? "<mark>" + w + "</mark>" : w);
  }}
  return (start > 0 && !endsSentence(words[start - 1]) ? "\u2026 " : "") + out.join(" ")
    + (end < words.length - 1 && !endsSentence(words[end]) ? " \u2026" : "");
}}

function pillTime(seconds) {{
  seconds = Math.floor(seconds);
  const h = Math.floor(seconds / 3600), m = Math.floor(seconds % 3600 / 60), s = seconds % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h ? h + ":" + two(m) + ":" + two(s) : two(m) + ":" + two(s);
}}

function buildCitation(result, sr, seconds) {{
  const meta = result.meta || {{}};
  const unlabeled = !sr.title || !sr.title.trim() || ["unattributed", "transcript", "discussion", "announcements"].includes(sr.title.trim().toLowerCase());
  const speaker = !unlabeled ? sr.title.trim() : (meta.participants || "Unidentified speaker");
  const videoTitle = (meta.series || "Techspressionism") + ": " + (meta.topic || "Untitled");
  const publisher = "Techspressionism Video Archive";
  const date = chicagoDate(meta.date);
  const timestamp = hhmmss(seconds);
  const url = (meta.youtube || "") + (meta.youtube ? "&t=" + Math.floor(seconds) + "s" : "");
  return speaker + ', "' + videoTitle + '," ' + publisher + ", " + date + ", streaming video, " + timestamp + ", " + url + ".";
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
        const m = (sr.url || "").match(/#t(\\d+)/);
        if (!yt || !m) continue;
        const seconds = parseInt(m[1], 10);
        const link = yt + "&t=" + Math.max(0, seconds - {watch_lead_in}) + "s";   // leads in; the citation below stays exact
        sr.excerpt = sentenceExcerpt(result, sr) || sr.excerpt;   // the whole sentence that matched, not a fixed-length snippet
        const citation = buildCitation(result, sr, seconds);
        const page = sr.url.split('#')[0];
        const here = page + (page.includes('?') ? '&' : '?') + 'play=1#t' + m[1];   // the page opens the transcript there and plays
        sr.excerpt = sr.excerpt
          + '<div class="citation-info"><strong>Citation information:</strong> '
          + '<span class="cite-text">' + escapeHtml(citation) + '</span> '
          + '<div class="cite-actions"><button type="button" class="copy-cite" data-citation="' + escapeHtml(citation) + '">Copy</button>'
          + '<a class="pill" href="' + escapeHtml(here) + '" title="Watch here: opens the transcript at this point and plays the video">' + PILL_SVG + pillTime(seconds) + '</a>'
          + '<a class="yt-jump" href="' + link + '" target="_blank" rel="noopener">Watch on YouTube &#8599;</a></div></div>';
      }}
      return result;
    }},
  }});
  // header search boxes on transcript pages send visitors here as ?q=term;
  // ?type=Interview preselects a media type
  const params = new URLSearchParams(location.search);
  setType(params.get("type") || "");
  const q = params.get("q");
  if (q) ui.triggerSearch(q);

  // phones: the filter dropdowns (Country, Speaker, Type, Year) sit behind one "Filters" button so the results start higher
  const searchBox = document.getElementById("search");
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
  browseSel.addEventListener("change", (ev) => choose(ev.target.value));

  // the header search box is the search box: it drives the results shown on this page
  const headerSearch = document.querySelector("header.site .hsearch");
  const headerInput = headerSearch.querySelector("input");
  headerInput.removeAttribute("required");
  headerSearch.addEventListener("submit", (ev) => ev.preventDefault());
  let searchTimer;
  headerInput.addEventListener("input", () => {{
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => ui.triggerSearch(headerInput.value.trim()), 150);
  }});
  if (q) headerInput.value = q;

  function setType(type) {{
    for (const b of document.querySelectorAll("#typebar a[data-type]")) {{
      b.setAttribute("aria-current", String(b.dataset.type === type));
    }}
    for (const g of document.querySelectorAll(".sessions-group")) {{
      g.hidden = !type || g.dataset.type !== type;      // no list until a category is chosen
    }}
    document.getElementById("browse-select").value = type;
    document.getElementById("rec-count").textContent = REC_COUNTS[type] || REC_COUNTS[""];   // the count and years follow the selected category
    // one control drives both the full-text search and the session list
    ui.triggerFilters(type ? {{ type: [type] }} : {{}});
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


def build_index(corpus):
    groups, spans = [], {}
    type_labels = [(t, TYPES[t]) for t in TYPES if any(x.get("type", "salon") == t for x in corpus)]
    for t, info in type_labels:
        entries = sorted((x for x in corpus if x.get("type", "salon") == t), key=lambda x: -x["number"])
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
            rows.append(
                f'<li><a href="{slug(entry)}.html">'
                f'<span class="num">#{entry["number"]}</span> {e(topic)}</a>{by} '
                f'<span class="d">{e(when)}</span></li>'
            )
        groups.append(
            f'<section class="sessions-group" data-type="{e(info["label"])}">'
            f'<ul class="sessions">' + "\n".join(rows) + "</ul></section>")
    def count_text(n, first, last):
        years = f"{first}\u2013{last}" if first != last else str(first)
        return f"{n} recording{'' if n == 1 else 's'} \u00b7 {years}"
    rec_counts = {label: count_text(*v) for label, v in spans.items()}
    if spans:
        rec_counts[""] = count_text(len(corpus), min(v[1] for v in spans.values()), max(v[2] for v in spans.values()))
    else:
        rec_counts[""] = f"{len(corpus)} recordings"

    latest_year = max((int(x["date_recorded"][:4]) for x in corpus if x.get("date_recorded")), default=2020)
    hours = round(sum(x.get("duration_seconds") or 0 for x in corpus) / 3600)
    return INDEX_TMPL.format(
        latest_year=latest_year,
        hours=hours,
        count_text=e(rec_counts[""]),
        rec_counts_js=json.dumps(rec_counts),
        groups="\n".join(groups),
        watch_lead_in=WATCH_LEAD_IN,
        header=build_header(corpus, ""),
        pill_svg_js=json.dumps(PILL_SVG),
    )


def main():
    no_index = "--no-index" in sys.argv
    with open(CORPUS_JSON) as f:
        corpus = json.load(f)

    NAV_CORPUS[:] = corpus
    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "style.css").write_text(STYLE + ("" if SITE_CONFIG.get("show_search_filters") else HIDE_FILTERS_CSS))
    (SITE_DIR / "index.html").write_text(add_robots(build_index(corpus), "index.html"))
    by_type = {}
    for entry in sorted(corpus, key=lambda x: -x["number"]):      # newest first, as on the home page
        by_type.setdefault(entry.get("type", "salon"), []).append(entry)
    for entry in corpus:
        (SITE_DIR / f"{slug(entry)}.html").write_text(add_robots(build_session_page(entry, by_type[entry.get('type', 'salon')]), f"{slug(entry)}.html"))

    if SITE_CONFIG.get("canonical_base"):       # sitemap.xml for search engines (only once the final address is known)
        urls = [canonical_url("")] + [canonical_url(f"{slug(x)}.html") for x in corpus]
        (SITE_DIR / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "".join(f"<url><loc>{e(u)}</loc></url>\n" for u in urls) + "</urlset>\n")
    else:
        (SITE_DIR / "sitemap.xml").unlink(missing_ok=True)

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
