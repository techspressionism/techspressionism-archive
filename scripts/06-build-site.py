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


# Each page links back to the recording's own page on techspressionism.com (data/site-pages.json).
_SITE_PAGES_PATH = ROOT / "data" / "site-pages.json"
SITE_PAGES = json.loads(_SITE_PAGES_PATH.read_text()).get("pages", {}) if _SITE_PAGES_PATH.exists() else {}


def site_page_html(entry):
    address = SITE_PAGES.get(slug(entry))
    return (f' &middot; <a href="{e(address)}" target="_blank" rel="noopener" data-pagefind-ignore>'
            f'View on techspressionism.com</a>') if address else ""


def add_robots(page_html):
    """While the archive is in beta (data/site-config.json: "beta_noindex": true) every page asks search
    engines not to list it. It stays fully usable for anyone with the address. Set to false at launch."""
    if not SITE_CONFIG.get("beta_noindex"):
        return page_html
    return page_html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n<meta name="robots" content="noindex, nofollow">', 1)


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


STYLE = """
:root { --fg:#1a1a1a; --muted:#666; --bg:#fafafa; --card:#fff; --accent:#c0392b; --line:#e2e2e2; }
* { box-sizing: border-box; }
body { margin:0; font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       color:var(--fg); background:var(--bg); }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
header.site { border-bottom:1px solid var(--line); background:var(--card); padding:.9rem 1.25rem; }
header.site .wrap { max-width:60rem; margin:0 auto; display:flex; gap:1rem; align-items:baseline; flex-wrap:wrap; }
header.site strong { font-size:1.05rem; }
header.site .hsearch { margin:0 0 0 auto; }
header.site .hsearch input { font:inherit; font-size:.9rem; width:15rem; max-width:100%; padding:.3rem .7rem; border:1px solid var(--line); border-radius:1rem; background:var(--bg); color:var(--fg); }
header.site .hsearch input:focus { outline:none; border-color:var(--accent); }
@media (max-width:34rem) { header.site .hsearch { flex:1 1 100%; } header.site .hsearch input { width:100%; } }
main { max-width:60rem; margin:0 auto; padding:1.5rem 1.25rem 4rem; }
h1 { font-size:1.7rem; margin:.2rem 0 .3rem; }
h1 .topic { color:var(--muted); font-weight:400; }
.meta { color:var(--muted); margin:.2rem 0 1.2rem; }
.speakers { list-style:none; padding:0; margin:0 0 1.5rem; display:flex; flex-wrap:wrap; gap:.4rem .8rem; }
.speakers li { background:var(--card); border:1px solid var(--line); border-radius:1rem; padding:.15rem .7rem; font-size:.9rem; }
.speakers .country { color:var(--muted); }
.flags { background:#fff8e1; border:1px solid #ffe08a; border-radius:.4rem; padding:.5rem .8rem; font-size:.88rem; color:#7a5c00; margin-bottom:1.5rem; }
section.seg { padding:.9rem 0; border-top:1px solid var(--line); }
.seg-head { display:flex; align-items:baseline; gap:.7rem; margin:0 0 .3rem; font-size:1rem; scroll-margin-top:5rem; }
.seg-head .speaker { font-weight:700; }
.seg-head .tc { font-size:.85rem; white-space:nowrap; }
section.seg p { margin:.3rem 0 0; }
a.suggest { font-size:.72rem; margin-left:.7rem; color:var(--muted); white-space:nowrap; opacity:.75; }
a.suggest:hover { opacity:1; color:var(--accent); }
/* index */
.sessions { list-style:none; padding:0; margin:1.5rem 0 0; }
.sessions li { border-bottom:1px solid var(--line); padding:.7rem 0; }
.sessions .num { display:inline-block; min-width:3.2rem; color:var(--muted); font-variant-numeric:tabular-nums; }
.sessions .d { color:var(--muted); font-size:.9rem; }
#search { margin:1rem 0 2rem; }
.pagefind-ui { --pagefind-ui-scale:.9; --pagefind-ui-primary:var(--accent); --pagefind-ui-font:inherit; }
.pagefind-ui mark { background:none; color:var(--accent); font-weight:700; padding:0; }
.intro { color:var(--muted); max-width:44rem; }
.intro .watch-ref { color:var(--accent); font-weight:600; }
.yt-jump { white-space:nowrap; font-size:.85em; margin-left:.3rem; }
.citation-info { margin-top:.5rem; padding:.5rem .7rem; background:var(--bg); border:1px solid var(--line); border-radius:.35rem; font-size:.85em; color:#333; }
.citation-info strong { display:block; margin-bottom:.2rem; color:var(--muted); font-size:.85em; font-weight:600; }
.citation-info .cite-text { font-family:Georgia,"Times New Roman",serif; }
.citation-info .copy-cite { display:block; margin-top:.4rem; font:inherit; font-size:.85em; padding:.2rem .6rem; border:1px solid var(--line); background:var(--card); border-radius:.3rem; cursor:pointer; }
.citation-info .copy-cite:hover { border-color:var(--accent); color:var(--accent); }
.cite { margin:2.5rem 0 0; padding:1rem 1.1rem; background:var(--card); border:1px solid var(--line); border-radius:.5rem; }
.cite h2 { font-size:.95rem; margin:0 0 .5rem; }
.cite blockquote { margin:0; font-size:.92rem; color:#333; }
.cite button { margin-top:.6rem; font:inherit; font-size:.82rem; padding:.25rem .7rem; border:1px solid var(--line); background:var(--bg); border-radius:.3rem; cursor:pointer; }
.cite button:hover { border-color:var(--accent); color:var(--accent); }
.cite .doi { color:var(--muted); }
.typebar { display:flex; flex-wrap:wrap; gap:.4rem; margin:1rem 0 .6rem; }
.typebar button { font:inherit; font-size:.9rem; padding:.3rem .85rem; border:1px solid var(--line); background:var(--card); border-radius:1rem; cursor:pointer; color:var(--fg); }
.typebar button:hover { border-color:var(--accent); }
.typebar button[aria-pressed="true"] { background:var(--accent); border-color:var(--accent); color:#fff; }
.typebar .n { opacity:.7; font-size:.8em; margin-left:.25rem; }
.sessions-group h3 { margin:1.6rem 0 0; font-size:1.05rem; }
.promo { margin:0 0 1.2rem; }
.promo img { width:100%; max-width:640px; height:auto; display:block; border-radius:.4rem; border:1px solid var(--line); }
"""

PAGE_TMPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Techspressionism Video Archive</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="site"><div class="wrap"><strong><a href="index.html">Techspressionism Video Archive</a></strong>
<form class="hsearch" action="index.html" method="get" role="search"><input type="search" name="q" placeholder="Search transcripts&hellip;" aria-label="Search transcripts" required></form></div></header>
<main>
<article data-pagefind-body>
{promo_image}
<h1 data-pagefind-meta="title:{meta_title}">{label} <span class="topic">{topic}</span></h1>
<p class="meta">
<span data-pagefind-filter="type:{type_cap}" data-pagefind-meta="type:{type_cap}">{type_cap}</span> &middot;
{date_word} <span data-pagefind-filter="year:{year}" data-pagefind-meta="date:{date_iso}">{recorded}</span>{moderator}{curator} &middot;
<a href="{url}" data-pagefind-meta="youtube:{url}">Watch on YouTube</a>{site_page}
<span data-pagefind-meta="video_id:{video_id}" hidden></span>
<span data-pagefind-meta="session:{number}" hidden></span>
<span data-pagefind-meta="series:{series}" hidden></span>
<span data-pagefind-meta="participants:{participants}" hidden></span>
<span data-pagefind-meta="topic:{topic_meta}" hidden></span>
</p>
{speakers}
{flags}
<div class="transcript">
{segments}
</div>
</article>
<section class="cite" data-pagefind-ignore>
<h2>Cite this session</h2>
<blockquote id="citation">{citation}</blockquote>
<button type="button" onclick="navigator.clipboard.writeText(document.getElementById('citation').innerText).then(()=>{{this.textContent='Copied';setTimeout(()=>this.textContent='Copy citation',1500)}})">Copy citation</button>
</section>
</main>
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


def build_promo_image(entry):
    """The salon's promo graphic (same image used as the YouTube thumbnail
    and techspressionism.com's featured image for that session), shown at
    the top of the transcript page only -- not on the index or in search
    results. Cached locally by Stage 1; not every session has one."""
    video_id = entry["video_id"]
    if not (THUMBNAILS_SRC_DIR / f"{video_id}.jpg").exists():
        return ""
    alt = e(f"Promo graphic for {label(entry)} — {entry.get('session_title') or 'Untitled'}")
    return f'<div class="promo" data-pagefind-ignore><img src="thumbnails/{e(video_id)}.jpg" alt="{alt}" loading="lazy"></div>'


def emphasize(escaped):
    """_Title_ markers from Stage 5 -> <em>. Runs on already-escaped text; a
    doubled underscore (the '[__]' blank-audio artifact) never matches."""
    return re.sub(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])", r"<em>\1</em>", escaped)


def build_session_page(entry):
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
        speakers_html = f'<ul class="speakers">{sp_items}</ul>{country_tags}'
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
    for seg in entry["segments"]:
        start = int(seg.get("start") or 0)
        speaker = seg.get("speaker") or ("Transcript" if all_unattributed else "Unattributed")
        yt = f"{url}&t={max(0, start - WATCH_LEAD_IN)}s"   # the link leads in; the printed time (below) is exact
        # paragraph breaks are real "\n\n" in the text (from pause-based
        # restoration or Zoom's own cue boundaries) -- browsers collapse
        # raw whitespace inside a single <p>, so they need to become actual
        # separate <p> elements or they render as one undifferentiated block
        paragraphs = [p.strip() for p in (seg.get("text") or "").split("\n\n") if p.strip()]
        paragraphs_html = "".join(f"<p>{emphasize(e(p))}{suggest_link(entry, start, p)}</p>" for p in paragraphs) or "<p></p>"
        seg_html.append(
            f'<section class="seg">'
            f'<h2 class="seg-head" id="t{start}">'
            f'<span class="speaker">{e(speaker)}</span>'
            f'<a class="tc" href="{e(yt)}" data-pagefind-ignore>{hhmmss(start)} &#9654; watch</a>'
            f'</h2>'
            f'{paragraphs_html}'
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
        promo_image=build_promo_image(entry),
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
<link rel="stylesheet" href="style.css">
<link href="pagefind/pagefind-ui.css" rel="stylesheet">
<script src="pagefind/pagefind-ui.js"></script>
</head>
<body>
<header class="site"><div class="wrap"><strong>Techspressionism Video Archive</strong>
<span class="d">{count} recordings &middot; {year_span}</span></div></header>
<main>
<p class="intro">A searchable, citable transcript archive of Techspressionism&rsquo;s recorded video: the monthly
<a href="https://techspressionism.com/Salon">Techspressionist Salon</a> (running since September 2020), artist
<a href="https://techspressionism.com/interviews/">interviews</a>, <a href="https://techspressionism.com/roundtable/">roundtables</a>,
and <a href="https://techspressionism.com/uzbekistan/media/videos/presentations/">presentations</a>. Search the full text
below &mdash; all recordings or just one type &mdash; or browse the list. Every result links to the transcript and to the exact moment in the recording.
Transcripts are machine-generated (Zoom, YouTube, and Whisper) and may contain errors &mdash; always verify a quote
via its <span class="watch-ref">&#9654;&nbsp;watch</span> link before citing. Built in Python with Claude Code.</p>
<div class="typebar" id="typebar" role="group" aria-label="Media type">{typebar}</div>
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
        const citation = buildCitation(result, sr, seconds);
        sr.excerpt = sr.excerpt
          + ' <a class="yt-jump" href="' + link + '" target="_blank" rel="noopener">&#9654; watch</a>'
          + '<div class="citation-info"><strong>Citation information:</strong> '
          + '<span class="cite-text">' + escapeHtml(citation) + '</span> '
          + '<button type="button" class="copy-cite" data-citation="' + escapeHtml(citation) + '">Copy</button></div>';
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

  document.getElementById("typebar").addEventListener("click", (ev) => {{
    const btn = ev.target.closest("button[data-type]");
    if (btn) setType(btn.dataset.type);
  }});

  function setType(type) {{
    for (const b of document.querySelectorAll("#typebar button")) {{
      b.setAttribute("aria-pressed", String(b.dataset.type === type));
    }}
    for (const g of document.querySelectorAll(".sessions-group")) {{
      g.hidden = !!type && g.dataset.type !== type;
    }}
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
<h2>Browse</h2>
{groups}
</main>
</body>
</html>
"""


def build_index(corpus):
    groups, buttons = [], []
    type_labels = [(t, TYPES[t]) for t in TYPES if any(x.get("type", "salon") == t for x in corpus)]
    buttons.append(f'<button type="button" data-type="" aria-pressed="true">All<span class="n">{len(corpus)}</span></button>')
    for t, info in type_labels:
        entries = sorted((x for x in corpus if x.get("type", "salon") == t), key=lambda x: -x["number"])
        rows = []
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
        buttons.append(
            f'<button type="button" data-type="{e(info["label"])}" aria-pressed="false">'
            f'{e(info["plural"])}<span class="n">{len(entries)}</span></button>')
        groups.append(
            f'<section class="sessions-group" data-type="{e(info["label"])}">'
            f'<h3>{e(info["plural"])} ({len(entries)})</h3><ul class="sessions">' + "\n".join(rows) + "</ul></section>")
    years = sorted(int(x["date_recorded"][:4]) for x in corpus
                   if x.get("date_recorded") and len(x["date_recorded"]) >= 7)
    return INDEX_TMPL.format(
        count=len(corpus),
        year_span=f"{years[0]}&ndash;{years[-1]}" if years else "",
        typebar="".join(buttons),
        groups="\n".join(groups),
        watch_lead_in=WATCH_LEAD_IN,
    )


def main():
    no_index = "--no-index" in sys.argv
    with open(CORPUS_JSON) as f:
        corpus = json.load(f)

    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "style.css").write_text(STYLE)
    (SITE_DIR / "index.html").write_text(add_robots(build_index(corpus)))
    for entry in corpus:
        (SITE_DIR / f"{slug(entry)}.html").write_text(add_robots(build_session_page(entry)))

    THUMBNAILS_OUT_DIR.mkdir(exist_ok=True)
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
