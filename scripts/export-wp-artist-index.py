#!/usr/bin/env python3
"""Make the text of the Techspressionist Visual Artist Index (the WPBakery text box on techspressionism.com/artists/)
with each artist's NAME linked to the artist's page in the archive.

    python3 scripts/export-wp-artist-index.py ~/Desktop/techspressionism.WordPress.<date>.xml
    python3 scripts/export-wp-artist-index.py <export.xml> --base https://techspressionism.com/archive/

The text box is the one with the class "tvai" in the page's [vc_column_text] blocks. Its HTML is kept exactly as it is
(the flag and website/Instagram icons, countries, states, everything), with one change: the name of an artist who has a page
in the archive becomes a link to it. A name that already links somewhere (their Southampton page, an interview page) is
re-pointed at the archive page. Names without an archive page are left as they are. An artist has an archive page when
they are heard or named in the recordings, so run 06-build-site.py first: the pages that exist in site/ decide it.

--base   where the archive is published. Default is the GitHub test site; use the final address for the WP Engine version.
Output   private/wp-artist-index/artist-index-<test|final>.html (paste it into the text box in the editor's Text/HTML view)
         and a report of what was linked. Nothing here is committed: the export file itself must never be (drafts, private pages).
"""
import html
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_BASE = "https://techspressionism.github.io/techspressionism-archive/"


def person_key(name):                      # the same rule the site build uses (06-build-site.py)
    s = unicodedata.normalize("NFKD", re.sub(r"\s*\([^)]*\)", "", name or "")).encode("ascii", "ignore").decode().lower()
    s = re.split(r"\s*/\s*", s)[0]
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()


def get_box(xml_path):
    xml = Path(xml_path).read_text(encoding="utf8", errors="ignore")
    if "<wp:" not in xml[:5000] and 'el_class="tvai"' in xml:              # a saved copy of the page text, not the whole export
        i = xml.find('el_class="tvai"')
        start = xml.find("]", i) + 1
        return xml[start:xml.find("[/vc_column_text]", start)]
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        it = m.group(1)
        if "<link>https://techspressionism.com/artists/</link>" not in it:
            continue
        content = re.search(r"<content:encoded><!\[CDATA\[(.*?)\]\]></content:encoded>", it, re.S).group(1)
        i = content.find('el_class="tvai"')
        start = content.find("]", i) + 1
        end = content.find("[/vc_column_text]", start)
        return content[start:end]
    sys.exit("The Artists page (https://techspressionism.com/artists/) is not in that export.")


def plain(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


SPLIT = re.compile(r"\s+[-–—](?:\s|$)|[-–—]\s")


def entry_name(line):
    """The artist's name at the start of an entry line: the visible text up to the first ' - ' (before the place)."""
    text = plain(line)
    head = re.split(r"\s+\|\s+", SPLIT.split(text, 1)[0])[0]           # "Name | Studio - Place" -> Name
    return head.strip("\u00a0 *") if text else ""


def variants(name):
    v = [name, name.replace("&", "&amp;"), name.replace("’", "&#8217;").replace("‘", "&#8216;"),
         name.replace("“", "&#8220;").replace("”", "&#8221;"), html.escape(name, quote=False),
         name.replace(" ", "&nbsp;"), name.replace("’", "'")]
    return list(dict.fromkeys(v))


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    base = TEST_BASE
    if "--base" in args:
        base = args[args.index("--base") + 1]
    base = base.rstrip("/") + "/"
    box = get_box(args[0])

    people = json.loads((ROOT / "data" / "people.json").read_text())["people"]
    by_key = {}
    for p in people:
        for n in [p["name"]] + p.get("aliases", []):
            by_key.setdefault(person_key(n), p)
    has_page = {p["id"] for p in people if (ROOT / "site" / f"artist-{p['id']}.html").exists()}

    out, linked, repointed, unmatched, no_page, failed = [], [], [], [], [], []
    for line in box.split("\n"):
        s = line.strip()
        if not s or s.startswith(("<h3", "<hr")) or "<img" in s[:1] :
            out.append(line)
            continue
        name = entry_name(line)
        if not name or len(name) > 70:
            out.append(line)
            continue
        p = by_key.get(person_key(name))
        if p is None:
            unmatched.append(name)
            out.append(line)
            continue
        if p["id"] not in has_page:
            no_page.append(name)
            out.append(line)
            continue
        url = base + f"artist-{p['id']}.html"
        new = None
        # a link that already carries the name (before the first icon): point it at the archive page
        icon = line.find("<img")
        for m in re.finditer(r'<a\s[^>]*href="([^"]*)"[^>]*>(.*?)</a>', line, re.S):
            if icon != -1 and m.start() > icon and "<img" not in m.group(2):
                break
            if "<img" in m.group(2):
                continue
            inner = plain(m.group(2))
            if inner and person_key(inner).split(" ")[0] in person_key(name).split(" ")[:1] + person_key(name).split(" "):
                new = line[:m.start(1)] + url + line[m.end(1):]
                repointed.append((name, m.group(1)))
                break
        if new is None:
            for v in variants(name):
                k = line.find(v)
                if k != -1:
                    new = line[:k] + f'<a href="{url}">{v}</a>' + line[k + len(v):]
                    linked.append(name)
                    break
        if new is None:
            failed.append(name)
            out.append(line)
        else:
            out.append(new)

    result = "\n".join(out)
    dest = ROOT / "private" / "wp-artist-index"
    dest.mkdir(parents=True, exist_ok=True)
    label = "test" if base == TEST_BASE else "final"
    path = dest / f"artist-index-{label}.html"
    path.write_text(result, encoding="utf8")
    report = [f"Linked to the archive: {len(linked)} names newly linked, {len(repointed)} existing links re-pointed.",
              f"Left as they were: {len(no_page)} names with no archive page (not heard or named in the recordings), "
              f"{len(unmatched)} lines whose name is not in the people directory, {len(failed)} that could not be placed.",
              "", "Existing links that were re-pointed (old address):"]
    report += [f"  {n}  <-  {u}" for n, u in repointed]
    report += ["", "Could not place:"] + [f"  {n}" for n in failed]
    report += ["", "Not in the people directory:"] + [f"  {n}" for n in unmatched]
    (dest / f"report-{label}.txt").write_text("\n".join(report) + "\n", encoding="utf8")
    print("\n".join(report[:2]))
    print(f"-> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
