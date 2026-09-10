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
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_JSON = ROOT / "corpus" / "corpus.json"
SITE_DIR = ROOT / "site"

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def e(s):
    return html.escape(str(s) if s is not None else "")


def facet(s):
    """A filter value that won't break Pagefind's generated <label for=...>
    (quotes) or its comma-separated filter parsing."""
    s = str(s or "")
    for ch in '"“”‘’,':
        s = s.replace(ch, "" if ch != "," else " ")
    return e(re.sub(r"\s+", " ", s).strip())


def fmt_date(iso):
    if not iso:
        return "date unknown"
    try:
        y, m, d = iso.split("-")
        return f"{int(d)} {MONTHS[int(m)]} {y}"
    except Exception:
        return iso


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
/* index */
.sessions { list-style:none; padding:0; margin:1.5rem 0 0; }
.sessions li { border-bottom:1px solid var(--line); padding:.7rem 0; }
.sessions .num { display:inline-block; min-width:3.2rem; color:var(--muted); font-variant-numeric:tabular-nums; }
.sessions .d { color:var(--muted); font-size:.9rem; }
#search { margin:1rem 0 2rem; }
.pagefind-ui { --pagefind-ui-scale:.9; --pagefind-ui-primary:var(--accent); --pagefind-ui-font:inherit; }
.intro { color:var(--muted); max-width:44rem; }
.yt-jump { white-space:nowrap; font-size:.85em; margin-left:.3rem; }
.cite { margin:2.5rem 0 0; padding:1rem 1.1rem; background:var(--card); border:1px solid var(--line); border-radius:.5rem; }
.cite h2 { font-size:.95rem; margin:0 0 .5rem; }
.cite blockquote { margin:0; font-size:.92rem; color:#333; }
.cite button { margin-top:.6rem; font:inherit; font-size:.82rem; padding:.25rem .7rem; border:1px solid var(--line); background:var(--bg); border-radius:.3rem; cursor:pointer; }
.cite button:hover { border-color:var(--accent); color:var(--accent); }
.cite .doi { color:var(--muted); }
"""

PAGE_TMPL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Techspressionist Salon Archive</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="site"><div class="wrap"><strong><a href="index.html">Techspressionist Salon Archive</a></strong></div></header>
<main>
<article data-pagefind-body>
<h1 data-pagefind-meta="title:{meta_title}">Salon {number} <span class="topic">{topic}</span></h1>
<p class="meta">
<span data-pagefind-filter="type:{type}" data-pagefind-meta="type:{type}">{type_cap}</span> &middot;
recorded <span data-pagefind-filter="year:{year}" data-pagefind-meta="date:{date_iso}">{recorded}</span>{moderator}{curator} &middot;
<a href="{url}" data-pagefind-meta="youtube:{url}">Watch on YouTube</a>
<span data-pagefind-meta="video_id:{video_id}" hidden></span>
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
    number = entry["number"]
    title = e(entry.get("session_title") or "Untitled")
    date = entry.get("date_recorded")
    if date:
        head = f'Techspressionist Salon {number}, &ldquo;{title},&rdquo; recorded {fmt_date(date)}.'
    else:
        head = f'Techspressionist Salon {number}, &ldquo;{title}.&rdquo;'
    return f'{head} <em>Techspressionist Salon Archive</em>. <span class="doi">[DOI pending Zenodo deposit]</span>'


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
    if entry.get("flags"):
        flags_html = (
            '<p class="flags" data-pagefind-ignore>Known gaps in this session\'s metadata: '
            + e(", ".join(entry["flags"]).replace("_", " ")) + ".</p>"
        )

    seg_html = []
    for seg in entry["segments"]:
        start = int(seg.get("start") or 0)
        speaker = seg.get("speaker") or "Unattributed"
        yt = f"{url}&t={start}s"
        seg_html.append(
            f'<section class="seg">'
            f'<h2 class="seg-head" id="t{start}">'
            f'<span class="speaker">{e(speaker)}</span>'
            f'<a class="tc" href="{e(yt)}" data-pagefind-ignore>{hhmmss(start)} &#9654; watch</a>'
            f'</h2>'
            f'<p>{e(seg.get("text") or "")}</p>'
            f'</section>'
        )

    moderator = f" &middot; moderated by {e(entry['moderator'])}" if entry.get("moderator") else ""
    curator = f" &middot; curated by {e(entry['curator'])}" if entry.get("curator") else ""

    return PAGE_TMPL.format(
        title=e(f"Salon {number} — {entry.get('session_title') or 'Untitled'}"),
        meta_title=e(f"Salon {number} — {entry.get('session_title') or 'Untitled'}"),
        type=e(stype),
        type_cap=e(stype.capitalize()),
        year=e(year),
        date_iso=e(entry.get("date_recorded") or ""),
        video_id=e(video_id),
        number=number,
        topic=e(entry.get("session_title") or ""),
        recorded=e(fmt_date(entry.get("date_recorded"))),
        moderator=moderator,
        curator=curator,
        url=e(url),
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
<title>Techspressionist Salon Archive</title>
<link rel="stylesheet" href="style.css">
<link href="pagefind/pagefind-ui.css" rel="stylesheet">
<script src="pagefind/pagefind-ui.js"></script>
</head>
<body>
<header class="site"><div class="wrap"><strong>Techspressionist Salon Archive</strong>
<span class="d">{count} recorded sessions &middot; earliest {first_date}</span></div></header>
<main>
<p class="intro">A searchable, citable transcript archive of the Techspressionist Salon &mdash;
a monthly gathering of artists working with technology, running since September 2020. Search the full
text below, or browse the session list. Every result links to the transcript and to the exact moment
in the recording.</p>
<div id="search"></div>
<script>
window.addEventListener('DOMContentLoaded', () => {{
  new PagefindUI({{
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
      // already resolve correctly under a project subpath. Just add a direct
      // deep-link to the matching second of the video on each sub-result.
      const yt = result.meta && result.meta.youtube;
      for (const sr of (result.sub_results || [])) {{
        const m = (sr.url || "").match(/#t(\\d+)/);
        if (yt && m) {{
          const link = yt + "&t=" + m[1] + "s";
          sr.excerpt = sr.excerpt + ' <a class="yt-jump" href="' + link + '" target="_blank" rel="noopener">&#9654; watch</a>';
        }}
      }}
      return result;
    }},
  }});
}});
</script>
<h2>All sessions</h2>
<ul class="sessions">
{rows}
</ul>
</main>
</body>
</html>
"""


def build_index(corpus):
    rows = []
    for entry in sorted(corpus, key=lambda x: -x["number"]):
        topic = entry.get("session_title") or "Untitled"
        rows.append(
            f'<li><a href="salon-{entry["number"]:03d}.html">'
            f'<span class="num">#{entry["number"]}</span> {e(topic)}</a> '
            f'<span class="d">{e(fmt_date(entry.get("date_recorded")))}</span></li>'
        )
    dates = sorted(x["date_recorded"] for x in corpus if x.get("date_recorded"))
    return INDEX_TMPL.format(
        count=len(corpus),
        first_date=fmt_date(dates[0]) if dates else "unknown",
        rows="\n".join(rows),
    )


def main():
    no_index = "--no-index" in sys.argv
    with open(CORPUS_JSON) as f:
        corpus = json.load(f)

    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "style.css").write_text(STYLE)
    (SITE_DIR / "index.html").write_text(build_index(corpus))
    for entry in corpus:
        (SITE_DIR / f"salon-{entry['number']:03d}.html").write_text(build_session_page(entry))
    print(f"Wrote {len(corpus) + 2} files to {SITE_DIR}")

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
