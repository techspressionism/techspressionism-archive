#!/usr/bin/env python3
"""Collect each recording's share picture (og:image) from the local video folders into assets/og/<video id>.jpg.

Looks in the recording's own folder (same folder rules as Stage 3: SALON_<n>, ROUNDTABLE_<n>, INTERVIEWS/<first name>) for the
website/Facebook picture Colin makes for it: a file ending _WP, _WS or _FB (jpg/jpeg/png), 1200x630. Preference: WP, then WS, then FB;
a file whose name carries a different recording number (SALON_105_WP.jpg inside SALON_106) is ignored. Recordings with nothing found are listed in
review/og-image-missing.csv; Stage 6 gives those pages the default share picture (assets/og-default.jpg) until a picture is added here.

    python3 scripts/collect-og-images.py           # copy what is found (assets/og is tracked in git: CI cannot see the local drive)

Second source, for recordings with no local file: the featured image of the recording's own page on techspressionism.com (the
"/video/..." page that embeds it), found in the WordPress export (WP_EXPORT below) and fetched one at a time, politely, into
raw/og-wp/. One that is not exactly 1200x630 is scaled to fit inside 1200x630 on white, never cropped. A featured image shared by
several recordings (a generic one) is flagged in the report: it is not specific to that recording.
Last resort: the recording's YouTube thumbnail (assets/thumbnails), fitted the same way. Everything is reported in review/og-image-report.csv (type, number, title, video id, source, note).
"""
import csv
import importlib.util
import json
import re
import shutil
from pathlib import Path

import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "assets" / "og"
PAT = re.compile(r"_(WP|WS|FB)\.(jpe?g|png)$", re.I)
RANK = {"WP": 0, "WS": 1, "FB": 2}
WP_EXPORT = Path.home() / "Downloads" / "techspressionism.WordPress.2026-09-22.xml"
WP_CACHE = ROOT / "raw" / "og-wp"
W, H = 1200, 630


def wp_featured_images():
    """video id -> featured-image URL of the recording's own /video/ page on techspressionism.com, from the WordPress export."""
    ns = {"wp": "http://wordpress.org/export/1.2/", "content": "http://purl.org/rss/1.0/modules/content/"}
    if not WP_EXPORT.exists():
        return {}
    ch = ET.parse(WP_EXPORT).getroot().find("channel")
    att, posts = {}, []
    for it in ch.findall("item"):
        kind, pid = it.find("wp:post_type", ns).text, it.find("wp:post_id", ns).text
        if kind == "attachment":
            u = it.find("wp:attachment_url", ns)
            att[pid] = u.text if u is not None else ""
            continue
        thumb = next((m.find("wp:meta_value", ns).text for m in it.findall("wp:postmeta", ns) if m.find("wp:meta_key", ns).text == "_thumbnail_id"), None)
        posts.append(((it.find("link").text or ""), it.find("wp:status", ns).text, thumb, it.find("content:encoded", ns).text or ""))
    out = {}
    for link, status, thumb, content in posts:
        if "/video/" not in link or status != "publish" or not thumb or not att.get(thumb):
            continue
        for vid in set(re.findall(r"(?:youtu\.be/|v=|embed/)([A-Za-z0-9_-]{11})", content)):
            out.setdefault(vid, att[thumb])
    return out


def fetch(url):
    """Download one picture into the cache (once), waiting a second after each real request."""
    WP_CACHE.mkdir(parents=True, exist_ok=True)
    dest = WP_CACHE / urllib.parse.unquote(url.rsplit("/", 1)[-1])
    if not dest.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Techspressionism archive build)"})
        with urllib.request.urlopen(req, timeout=60) as r:
            dest.write_bytes(r.read())
        time.sleep(1)
    return dest


def fit_to_frame(im):
    """Scale to fit inside 1200x630 without cropping; white where the picture does not reach."""
    im = im.convert("RGB")
    if im.size == (W, H):
        return im
    k = min(W / im.width, H / im.height)
    fitted = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    canvas.paste(fitted, ((W - fitted.width) // 2, (H - fitted.height) // 2))
    return canvas


def stage3():
    spec = importlib.util.spec_from_file_location("stage3", HERE / "03-whisper-transcribe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    m = stage3()
    OUT.mkdir(parents=True, exist_ok=True)
    (ROOT / "review").mkdir(exist_ok=True)
    sessions = json.load(open(ROOT / "data" / "sessions.json"))
    wp = wp_featured_images()
    shared = Counter(wp.values())
    report = []
    for s in sessions:
        head = [s["type"], s["number"], s.get("session_title") or s.get("interviewee") or "", s["video_id"]]
        dest = OUT / f"{s['video_id']}.jpg"
        cands = []
        for root in m.local_search_roots(s):
            if not root.is_dir():
                continue
            for p in root.rglob("*"):
                mt = PAT.search(p.name)
                if not (p.is_file() and mt) or p.name.startswith("."):
                    continue
                num = re.search(r"(\d+)_(?:WP|WS|FB)\.", p.name, re.I)
                if num and s["type"] in ("salon", "roundtable") and int(num.group(1)) != int(s["number"]):
                    continue
                try:
                    size = Image.open(p).size
                except Exception:
                    continue
                cands.append((size != (W, H), RANK[mt.group(1).upper()], str(p), p))
        if cands:
            _, _, _, best = sorted(cands)[0]
            with Image.open(best) as im:
                fit_to_frame(im).save(dest, "JPEG", quality=90, optimize=True, progressive=True)
            report.append(head + ["local file", best.name])
            continue
        url = wp.get(s["video_id"])
        if url:
            try:
                f = fetch(url)
                with Image.open(f) as im:
                    size = im.size
                    fit_to_frame(im).save(dest, "JPEG", quality=90, optimize=True, progressive=True)
                note = url.rsplit("/", 1)[-1] + (f" ({size[0]}x{size[1]}, fitted on white)" if size != (W, H) else "")
                if shared[url] > 1:
                    note += f"; the same picture is used by {shared[url]} recordings (generic)"
                report.append(head + ["techspressionism.com featured image", note])
                continue
            except Exception as ex:
                report.append(head + ["default (OG.jpg)", f"WP featured image could not be fetched: {ex}"])
                continue
        yt = ROOT / "assets" / "thumbnails" / f"{s['video_id']}.jpg"      # last resort before the default: the recording's own YouTube thumbnail
        if yt.exists():
            with Image.open(yt) as im:
                if im.width >= 200:      # a 120 px one is YouTube's grey "no picture" placeholder
                    fit_to_frame(im).save(dest, "JPEG", quality=90, optimize=True, progressive=True)
                    report.append(head + ["YouTube thumbnail", f"{im.width}x{im.height}, fitted on white"])
                    continue
        report.append(head + ["default (OG.jpg)", "no picture found locally, on techspressionism.com or on YouTube"])
    with open(ROOT / "review" / "og-image-report.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["type", "number", "title", "video_id", "source", "note"])
        w.writerows(report)
    c = Counter(r[4] for r in report)
    print(dict(c))


if __name__ == "__main__":
    main()
