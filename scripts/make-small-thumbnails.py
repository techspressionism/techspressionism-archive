#!/usr/bin/env python3
"""Make the small thumbnails (240 px wide) used in the site's "All Salons" sidebar lists, from the
full-size promo graphics in assets/thumbnails/. Only missing ones are made. Uses macOS `sips`
(built in, nothing to install). The small files are committed (assets/thumbnails-small/) because the
site is built on GitHub, which has no access to the originals' source folder or to sips.

    python3 scripts/make-small-thumbnails.py
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC, OUT = ROOT / "assets" / "thumbnails", ROOT / "assets" / "thumbnails-small"
OUT.mkdir(exist_ok=True)
made = 0
for src in sorted(SRC.glob("*.jpg")):
    dest = OUT / src.name
    if dest.exists():
        continue
    subprocess.run(["sips", "-Z", "240", "-s", "formatOptions", "70", str(src), "--out", str(dest)],
                   check=True, capture_output=True)
    made += 1
print(f"made {made} small thumbnails ({len(list(OUT.glob('*.jpg')))} in total) in {OUT.relative_to(ROOT)}")
