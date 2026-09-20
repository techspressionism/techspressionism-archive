#!/usr/bin/env python3
"""Write data/recordings.csv: one row per recording with its title, dates, YouTube address and the page on
techspressionism.com. It is part of the Zenodo deposit, so that the permanent record says where every video lives
(the citations in the archive use those YouTube addresses). No email addresses, no private data.

    python3 scripts/export-recordings-csv.py
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
corpus = json.loads((ROOT / "corpus" / "corpus.json").read_text())
pages = json.loads((ROOT / "data" / "site-pages.json").read_text())
pages = pages.get("pages", pages) if isinstance(pages, dict) else {}
TYPE_ORDER = ["salon", "interview", "roundtable", "presentation"]

rows = []
for e in sorted(corpus, key=lambda x: (TYPE_ORDER.index(x.get("type", "salon")), x["number"])):
    slug = f"{e.get('type', 'salon')}-{int(e['number']):03d}"
    rows.append({
        "id": slug, "type": e.get("type", "salon"), "number": e["number"],
        "title": e.get("session_title") or "",
        "date_recorded": e.get("date_recorded") or "", "date_published": e.get("date_published") or "",
        "youtube_video_id": e["video_id"], "youtube_url": e["url"],
        "techspressionism_page": pages.get(slug, ""),
        "duration_seconds": e.get("duration_seconds") or "",
        "transcript_source": e.get("transcript_source") or "",
    })
out = ROOT / "data" / "recordings.csv"
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"wrote {len(rows)} recordings to {out.relative_to(ROOT)}")
