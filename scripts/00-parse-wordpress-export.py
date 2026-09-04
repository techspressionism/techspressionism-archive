#!/usr/bin/env python3
"""Parse a WordPress WXR export and cache cleaned text for every Salon post.

This replaces live browser/scraping of techspressionism.com as the secondary
metadata source for Stage 1 -- the export is authoritative, complete, and
doesn't hit Cloudflare bot protection.

Usage:
    python3 scripts/00-parse-wordpress-export.py /path/to/export.xml
"""
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_TEXT_DIR = ROOT / "raw" / "site_text"
CACHE_PATH = ROOT / "raw" / "wordpress_salon_posts.json"

NS = {
    "wp": "http://wordpress.org/export/1.2/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "excerpt": "http://purl.org/rss/1.0/modules/excerpt/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# vc_raw_html payloads are base64-encoded HTML blobs (usually anchor/spacer
# boilerplate) -- drop the whole block rather than leaving encoded junk behind.
RAW_HTML_BLOCK_RE = re.compile(r"\[vc_raw_html\].*?\[/vc_raw_html\]", re.DOTALL)
# Generic shortcode tag, any plugin prefix (vc_, ult_buttons, etc). Replaced
# with a newline, not deleted outright, since shortcode boundaries usually
# mark the edge of a text block (columns, sections) and gluing the
# surrounding text together without a separator corrupts adjacent sentences.
SHORTCODE_RE = re.compile(r"\[/?[a-zA-Z_][\w-]*(?:\s[^\[\]]*)?\]")
BLOCK_CLOSE_RE = re.compile(r"</(p|div|h[1-6]|li|tr)>", re.IGNORECASE)
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
BLANK_RUN_RE = re.compile(r"\n{3,}")


def clean_content(raw_html):
    text = RAW_HTML_BLOCK_RE.sub("\n", raw_html)
    text = SHORTCODE_RE.sub("\n", text)
    text = BR_RE.sub("\n", text)
    text = BLOCK_CLOSE_RE.sub("\n", text)
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    text = BLANK_RUN_RE.sub("\n\n", text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def parse_export(xml_path):
    tree = ET.parse(xml_path)
    channel = tree.getroot().find("channel")
    posts = {}
    for item in channel.findall("item"):
        post_type = item.findtext("wp:post_type", namespaces=NS)
        if post_type != "post":
            continue
        nicenames = [c.get("nicename") for c in item.findall("category")]
        if "salon" not in nicenames:
            continue

        slug = item.findtext("wp:post_name", namespaces=NS)
        title = item.findtext("title")
        link = item.findtext("link")
        post_date = item.findtext("wp:post_date", namespaces=NS)
        raw_content = item.findtext("content:encoded", namespaces=NS) or ""

        posts[slug] = {
            "slug": slug,
            "title": title,
            "link": link,
            "post_date": post_date,
            "content_clean": clean_content(raw_content),
        }
    return posts


def main():
    if len(sys.argv) != 2:
        print("Usage: 00-parse-wordpress-export.py <path-to-wxr.xml>", file=sys.stderr)
        sys.exit(1)

    xml_path = Path(sys.argv[1])
    posts = parse_export(xml_path)
    print(f"Found {len(posts)} Salon posts in export")

    SITE_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    for slug, post in posts.items():
        (SITE_TEXT_DIR / f"{slug}.txt").write_text(post["content_clean"] + "\n")

    with open(CACHE_PATH, "w") as f:
        json.dump(posts, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(posts)} cleaned text files to {SITE_TEXT_DIR}")
    print(f"Wrote structured cache to {CACHE_PATH}")


if __name__ == "__main__":
    main()
