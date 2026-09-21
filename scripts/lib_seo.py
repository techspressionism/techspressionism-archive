"""Search-engine and AI-discovery markup for the archive's pages (used by 06-build-site.py).

What each page gets, once the archive has its final address (data/site-config.json "canonical_base"):
  * a description, Open Graph and Twitter card tags (share previews use the recording's promo image),
  * schema.org JSON-LD: WebSite/Organization on every page, VideoObject + Clips + BreadcrumbList on a recording,
    ProfilePage + Person on an artist page, Dataset + SearchAction on the home page, AboutPage on the About page,
  * Dublin Core and citation_* tags so Zotero and scholarly search can read a recording as a citable item,
  * a rel="alternate" link to the recording's plain-Markdown transcript.
Site-wide: sitemap.xml (with the video extension), llms.txt, robots.txt (see the manual for the WP Engine root files).
Without a canonical_base the tags that need an absolute address (canonical, og:url, og:image, JSON-LD) are left out;
the title and description are always written.
"""
import datetime
import html
import json
import re


def esc(s):
    return html.escape(str(s) if s is not None else "", quote=True)


def clip_text(text, limit=158):
    """Cut at a word boundary, for a description."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:–— ") + "…"


def iso_duration(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return "PT" + (f"{h}H" if h else "") + (f"{m}M" if m else "") + (f"{s}S" if s or not (h or m) else "")


def ld_script(obj):
    return '<script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + "</script>"


def head_tags(*, title, description, url="", image="", og_type="website", site_name="", jsonld=None, meta=(), alternates=(),
              video_embed="", image_size=("1280", "720")):
    """The tags inserted before </head>. `meta` is [(name, content)], `alternates` is [(mime type, href, title)]."""
    t = [f'<meta name="description" content="{esc(description)}">']
    og = [("og:site_name", site_name), ("og:title", title), ("og:description", description), ("og:type", og_type),
          ("og:locale", "en_US")]
    if url:
        og.append(("og:url", url))
    if image:
        og += [("og:image", image), ("og:image:width", image_size[0]), ("og:image:height", image_size[1])]
    if video_embed:
        og += [("og:video", video_embed), ("og:video:secure_url", video_embed), ("og:video:type", "text/html"),
               ("og:video:width", "1280"), ("og:video:height", "720")]
    t += [f'<meta property="{k}" content="{esc(v)}">' for k, v in og if v]
    tw = [("twitter:card", "summary_large_image" if image else "summary"), ("twitter:title", title),
          ("twitter:description", description)]
    if image:
        tw.append(("twitter:image", image))
    t += [f'<meta name="{k}" content="{esc(v)}">' for k, v in tw]
    t += [f'<meta name="{esc(k)}" content="{esc(v)}">' for k, v in meta if v]
    t += [f'<link rel="alternate" type="{esc(mime)}" href="{esc(href)}" title="{esc(ttl)}">' for mime, href, ttl in alternates]
    if jsonld:
        t.append(ld_script(jsonld))
    return "\n".join(t)


def site_nodes(base, brand, org_name, org_url):
    """WebSite and Organization, the same on every page (referred to by @id)."""
    return [
        {"@type": "WebSite", "@id": base + "#website", "url": base, "name": brand, "inLanguage": "en",
         "publisher": {"@id": base + "#org"}},
        {"@type": "Organization", "@id": base + "#org", "name": org_name, "url": org_url},
    ]


def breadcrumb(page_url, trail):
    """trail = [(name, url), ...] ending with the page itself."""
    return {"@type": "BreadcrumbList", "@id": page_url + "#breadcrumb",
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": u} for i, (n, u) in enumerate(trail)]}


def video_graph(*, base, brand, org_name, org_url, page_url, name, description, thumb, video_id, upload_date, recorded,
                duration, series_name, people, clips, trail):
    """A recording: WebPage about a VideoObject (with Clips per speaker) and its BreadcrumbList."""
    video = {"@type": "VideoObject", "@id": page_url + "#video", "name": name, "description": description,
             "thumbnailUrl": [thumb] if thumb else [], "uploadDate": upload_date, "duration": iso_duration(duration),
             "embedUrl": f"https://www.youtube.com/embed/{video_id}", "sameAs": f"https://www.youtube.com/watch?v={video_id}",
             "inLanguage": "en", "publisher": {"@id": base + "#org"}, "mainEntityOfPage": {"@id": page_url + "#webpage"},
             "isPartOf": {"@type": "CreativeWorkSeries", "name": series_name}}
    if recorded:
        video["dateCreated"] = recorded
    if people:
        video["actor"] = [({"@type": "Person", "name": n, **({"url": u} if u else {})}) for n, u in people]
    if clips:
        video["hasPart"] = [{"@type": "Clip", "name": n, "startOffset": s, "endOffset": e, "url": f"{page_url}#t{s}"} for n, s, e in clips]
    graph = site_nodes(base, brand, org_name, org_url) + [
        {"@type": "WebPage", "@id": page_url + "#webpage", "url": page_url, "name": name, "description": description,
         "inLanguage": "en", "isPartOf": {"@id": base + "#website"}, "breadcrumb": {"@id": page_url + "#breadcrumb"},
         "mainEntity": {"@id": page_url + "#video"}, **({"primaryImageOfPage": {"@type": "ImageObject", "url": thumb}} if thumb else {})},
        breadcrumb(page_url, trail), video]
    return {"@context": "https://schema.org", "@graph": graph}


def person_graph(*, base, brand, org_name, org_url, page_url, name, description, aliases, same_as, appearances, trail):
    person = {"@type": "Person", "@id": page_url + "#person", "name": name, "url": page_url}
    if aliases:
        person["alternateName"] = aliases
    if same_as:
        person["sameAs"] = same_as
    if appearances:
        person["subjectOf"] = [{"@type": "VideoObject", "name": n, "url": u} for n, u in appearances]
    graph = site_nodes(base, brand, org_name, org_url) + [
        {"@type": "ProfilePage", "@id": page_url + "#webpage", "url": page_url, "name": name, "description": description,
         "inLanguage": "en", "isPartOf": {"@id": base + "#website"}, "breadcrumb": {"@id": page_url + "#breadcrumb"},
         "mainEntity": {"@id": page_url + "#person"}},
        breadcrumb(page_url, trail), person]
    return {"@context": "https://schema.org", "@graph": graph}


def home_graph(*, base, brand, org_name, org_url, description, first_year, last_year, csv_url, license_url, doi, youtube_channel):
    dataset = {"@type": "Dataset", "@id": base + "#dataset", "name": f"{brand}: transcripts of Techspressionism recordings",
               "description": description, "url": base, "inLanguage": "en", "isAccessibleForFree": True,
               "creator": {"@id": base + "#org"}, "publisher": {"@id": base + "#org"},
               "temporalCoverage": f"{first_year}/{last_year}",
               "keywords": ["Techspressionism", "art and technology", "digital art", "artist talks", "oral history", "transcripts"],
               "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": csv_url}] if csv_url else []}
    if license_url:
        dataset["license"] = license_url
    if doi:
        dataset["identifier"] = doi
        dataset["sameAs"] = doi if doi.startswith("http") else f"https://doi.org/{doi}"
    nodes = site_nodes(base, brand, org_name, org_url)
    nodes[0]["potentialAction"] = {"@type": "SearchAction", "target": {"@type": "EntryPoint", "urlTemplate": base + "?q={search_term_string}"},
                                   "query-input": "required name=search_term_string"}
    if youtube_channel:
        nodes[1]["sameAs"] = [youtube_channel]
    return {"@context": "https://schema.org", "@graph": nodes + [
        {"@type": "WebPage", "@id": base + "#webpage", "url": base, "name": brand, "description": description, "inLanguage": "en",
         "isPartOf": {"@id": base + "#website"}, "about": {"@id": base + "#dataset"}}, dataset]}


def about_graph(*, base, brand, org_name, org_url, page_url, description, trail):
    graph = site_nodes(base, brand, org_name, org_url) + [
        {"@type": "AboutPage", "@id": page_url + "#webpage", "url": page_url, "name": f"About the {brand}", "description": description,
         "inLanguage": "en", "isPartOf": {"@id": base + "#website"}, "breadcrumb": {"@id": page_url + "#breadcrumb"}}, breadcrumb(page_url, trail)]
    return {"@context": "https://schema.org", "@graph": graph}


def scholar_meta(*, title, authors, recorded, publisher, url, source_url):
    """Dublin Core + citation_* tags (Zotero, Google Scholar-style readers)."""
    m = [("DC.title", title), ("DC.type", "Text"), ("DC.format", "text/html"), ("DC.language", "en"), ("DC.publisher", publisher),
         ("DC.identifier", url), ("DC.source", source_url), ("DC.date", recorded or ""),
         ("citation_title", title), ("citation_publisher", publisher), ("citation_language", "en"),
         ("citation_abstract_html_url", url)]
    m += [("DC.creator", a) for a in authors] + [("citation_author", a) for a in authors]
    if recorded:
        m.append(("citation_publication_date", recorded.replace("-", "/")))
    return m


# ---- site-wide files -------------------------------------------------------------------------------------------------

def sitemap_xml(pages):
    """pages = [{"loc":..., "video": {...} or None}]. The video extension helps video search list the recordings."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">']
    for p in pages:
        out.append(f"<url><loc>{esc(p['loc'])}</loc>" + (f"<lastmod>{esc(p['lastmod'])}</lastmod>" if p.get("lastmod") else ""))
        v = p.get("video")
        if v:
            out.append("<video:video>"
                       f"<video:thumbnail_loc>{esc(v['thumb'])}</video:thumbnail_loc><video:title>{esc(v['title'])}</video:title>"
                       f"<video:description>{esc(v['description'])}</video:description>"
                       f"<video:player_loc>{esc(v['embed'])}</video:player_loc><video:duration>{int(v['duration'])}</video:duration>"
                       f"<video:publication_date>{esc(v['published'])}</video:publication_date>"
                       "<video:family_friendly>yes</video:family_friendly><video:live>no</video:live></video:video>")
        out.append("</url>")
    out.append("</urlset>")
    return "\n".join(out) + "\n"


AI_AGENTS = [  # crawlers of AI search and assistants (and of model training), named so the choice is explicit
    "OAI-SearchBot", "ChatGPT-User", "GPTBot", "Claude-SearchBot", "Claude-User", "ClaudeBot", "PerplexityBot",
    "Perplexity-User", "Google-Extended", "Applebot-Extended", "CCBot", "Bytespider", "Amazonbot", "meta-externalagent", "cohere-ai",
]


def robots_txt(sitemap_url, allow_training=True):
    lines = ["User-agent: *", "Allow: /", ""]
    for a in AI_AGENTS:
        train = a in ("GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended", "CCBot", "Bytespider", "meta-externalagent", "cohere-ai")
        lines += [f"User-agent: {a}", "Allow: /" if (allow_training or not train) else "Disallow: /", ""]
    lines.append(f"Sitemap: {sitemap_url}")
    return "\n".join(lines) + "\n"


def llms_txt(*, brand, summary, base, about_url, csv_url, groups, artists_url, sitemap_url, doi):
    """The llmstxt.org format: an H1, a blockquote summary, then sections of links. `groups` = [(heading, [(title, url, note)])]."""
    out = [f"# {brand}", "", f"> {summary}", "",
           "Every transcript passage is timestamped against the recording on YouTube; cite the YouTube address and the timestamp. "
           "Transcripts are machine-generated and contain errors: check quotations against the video. "
           "Passages whose speaker is not confirmed are marked Unattributed.", "",
           "## About", "", f"- [About the archive, coverage, method and how to cite]({about_url})",
           f"- [Recordings as a table (CSV)]({csv_url})", f"- [Artists heard or named in the recordings]({artists_url})",
           f"- [Sitemap]({sitemap_url})"]
    if doi:
        out.append(f"- [Permanent deposit (DOI)]({doi if doi.startswith('http') else 'https://doi.org/' + doi})")
    for heading, items in groups:
        out += ["", f"## {heading}", ""]
        out += [f"- [{t}]({u}): {n}" if n else f"- [{t}]({u})" for t, u, n in items]
    return "\n".join(out) + "\n"


def today():
    return datetime.date.today().isoformat()
