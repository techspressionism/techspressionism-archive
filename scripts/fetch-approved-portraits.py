#!/usr/bin/env python3
"""Pull the portraits Colin published in the review tool (techspressionism.com/ts-portraits/) into the archive build:
data/artist-portraits.json + assets/artist-portraits/<id>.jpg. Run before every build that will be pushed live
(push-live.sh does this), otherwise the rebuilt artist pages would drop the published portraits.
Needs network (run on the Mac, not in the cloud container). Exits non-zero if the site can't be read, so a push stops
instead of wiping portraits."""
import json, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://techspressionism.com/wp-json/ts-portraits/v1/approved"
OUT_JSON = ROOT / "data" / "artist-portraits.json"
OUT_DIR = ROOT / "assets" / "artist-portraits"
# artist pages that were merged into another one (same person, two names): a portrait published on the old id moves along
MOVED = {"cristina-inciarte": "cristina-ballesteros", "gregory-patrick-garvey": "greg-garvey", "carolina-ramonde": "caro-ramonde", "adrienne-wortzel": "adrianne-wortzel", "carmen-payasa-moses": "cee-moses"}   # maiden / married name (Colin 2026-11-21)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ts-archive-build"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main():
    try:
        approved = json.loads(get(API))
    except Exception as ex:
        sys.exit(f"could not read {API}: {ex}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    old = json.loads(OUT_JSON.read_text()) if OUT_JSON.exists() else {}
    new = {}
    for pid, a in sorted(approved.items()):
        pid = MOVED.get(pid, pid)
        f = OUT_DIR / f"{pid}.jpg"
        same = old.get(pid, {}).get("opt") == a["opt"] and old.get(pid, {}).get("recording") == a["recording"] and f.exists()
        if not same:
            f.write_bytes(get(a["final"]))
        new[pid] = {"file": f"{pid}.jpg", "opt": a["opt"], "recording": a["recording"], "t": a["t"] or 0}
    for pid in set(old) - set(new):          # removed in the tool: drop it here too
        (OUT_DIR / old[pid]["file"]).unlink(missing_ok=True)
    OUT_JSON.write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(new)} published portraits ({len(set(new) - set(old))} new, {len(set(old) - set(new))} removed)")


if __name__ == "__main__":
    main()
