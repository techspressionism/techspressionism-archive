#!/usr/bin/env python3
"""Write the files that go with the recordings' descriptive addresses (salon-081-open-studios/ ...):

    python3 scripts/make-address-files.py

  wordpress/archive-address-map.csv   every recording: id, old address, new address
  wordpress/yoast-redirects.csv       permanent (301) redirects old -> new, for Yoast SEO Premium > Redirects > Import
  wordpress/tva-view-transcript.php   the View transcript plugin's video-id list, pointing at the new addresses

Run it after a recording is added or a title changes, then re-import the redirects / update the plugin file.
"""
import csv
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("site06", ROOT / "scripts" / "06-build-site.py")
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)
BASE = "/archive"

corpus = json.loads((ROOT / "corpus" / "corpus.json").read_text())
rows = []
for x in corpus:
    old = site.slug(x)
    rows.append({"id": old, "video_id": x["video_id"], "old": f"{BASE}/{old}/", "new": f"{BASE}/{site.page_name(x)}/"})
rows.sort(key=lambda r: r["id"])

with open(ROOT / "wordpress" / "archive-address-map.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["id", "video_id", "old", "new"])
    w.writeheader()
    w.writerows(rows)
with open(ROOT / "wordpress" / "yoast-redirects.csv", "w", newline="") as f:
    w = csv.writer(f)
    for r in rows:
        if r["old"] != r["new"]:
            w.writerow([r["old"], r["new"], 301, "plain"])

php = ROOT / "wordpress" / "tva-view-transcript.php"
text = php.read_text()
lines = "".join(f"    '{r['video_id']}' => '{r['new'].split('/')[2]}',\n" for r in rows)
text = re.sub(r"(function tva_transcript_map\(\) \{\n  return array\(\n).*?(  \);)", lambda m: m.group(1) + lines + m.group(2), text, flags=re.S)
php.write_text(text)
print(f"{len(rows)} recordings -> wordpress/archive-address-map.csv, yoast-redirects.csv, tva-view-transcript.php")
