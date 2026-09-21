#!/usr/bin/env python3
"""Import corrections submitted through the "Suggest a correction" Gravity Form.

    python3 scripts/import-suggestions.py entries.csv            # export from WordPress: Forms > Entries > Export
    python3 scripts/import-suggestions.py entries.csv --dry-run  # show what would happen, write nothing

Each row carries the recording, the time, the passage as it was pre-filled, and the corrected passage.
The importer finds that paragraph in the current transcript and files the correction as a PENDING
suggestion in raw/suggestions/<slug>.json (git-ignored: it can hold names and notes). Nothing is applied.
Review them in TextReview (Suggestions): approving one writes an edit to data/text-edits/, which is
what the page builder applies and what is committed.

Privacy: the export's email column is never read. A name is kept for credit only when the submitter
ticked the consent box ("credit"); a note has any email address removed. Re-importing the same file is
safe: entries already imported (by Gravity Forms entry id) are skipped.

Columns are found by the words in their headings (Recording, Time, Passage, Corrected passage, Note,
Name, Credit, Entry Id, Entry Date), so the labels only need to be recognisable. The detected mapping
is printed so a mismatch is obvious.
"""
import csv
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import textreview  # noqa: E402  (its corpus parser; importing it starts nothing)

OUT_DIR = ROOT / "raw" / "suggestions"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
NO_WORDS = {"", "no", "n", "0", "false", "off", "none"}
LINK_RE = re.compile(r"https?://|www\.", re.I)


def find_col(headers, must, avoid=()):
    for h in headers:
        low = h.lower()
        if all(k in low for k in must) and not any(a in low for a in avoid):
            return h
    return None


def detect(headers):
    cols = {
        "recording": find_col(headers, ("recording",)),
        "time": find_col(headers, ("time",), avoid=("date",)),
        "passage": find_col(headers, ("passage",), avoid=("correct",)),
        "correction": find_col(headers, ("correct", "passage")) or find_col(headers, ("corrected",)),
        "note": find_col(headers, ("note",)),
        "consent": find_col(headers, ("credit",)),
        "entry_id": find_col(headers, ("entry id",)) or find_col(headers, ("id",), avoid=("recording",)),
        "date": find_col(headers, ("entry date",)) or find_col(headers, ("date created",)),
    }
    cols["name"] = [h for h in headers if h.lower().startswith("name")]
    return cols


def words_span(text, n):
    """Character offset just after the n-th word of text."""
    spans = [m.end() for m in re.finditer(r"\S+", text)]
    return spans[n - 1] if 0 < n <= len(spans) else len(text)


def find_paragraph(slug, passage, t):
    """(start, paragraph, how) for the paragraph the passage came from, or (None, None, reason)."""
    paras = [(b["start"], p) for b in textreview.parse_corpus(slug) for p in b["paras"]]
    norm = lambda s: " ".join(s.split())
    pn = norm(passage)
    near = lambda pool: min(pool, key=lambda sp: abs(sp[0] - t))
    exact = [sp for sp in paras if norm(sp[1]) == pn]
    if exact:
        return (*near(exact), "exact")
    prefix = [sp for sp in paras if len(pn) >= 40 and norm(sp[1]).startswith(pn)]   # the passage was cut for the address
    if prefix:
        return (*near(prefix), "prefix")
    close = [(difflib.SequenceMatcher(None, pn, norm(p)).ratio(), s, p) for s, p in paras if abs(s - t) <= 180]
    best = max(close, default=None)
    if best and best[0] >= 0.92:
        return best[1], best[2], "approximate"
    return None, None, "the passage is no longer in the transcript (it may have been rebuilt or corrected)"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        sys.exit(__doc__)
    with open(args[0], newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows, headers = list(reader), reader.fieldnames or []
    cols = detect(headers)
    print("columns found:", {k: v for k, v in cols.items() if v})
    missing = [k for k in ("recording", "time", "passage", "correction") if not cols[k]]
    if missing:
        sys.exit(f"cannot find the column(s) for: {', '.join(missing)}\nheadings in the file: {headers}")

    seen = {}
    counts = {"imported": 0, "no change": 0, "already imported": 0, "unmatched": 0, "bad row": 0, "repeat (merged)": 0, "spam-like": 0}
    for row in rows:
        slug = (row.get(cols["recording"]) or "").strip()
        if not textreview.SLUG_RE.match(slug) or not (textreview.CORPUS_DIR / f"{slug}.md").exists():
            counts["bad row"] += 1
            print(f"  skipped: unknown recording {slug!r}")
            continue
        try:
            t = float(row.get(cols["time"]) or 0)
        except ValueError:
            t = 0.0
        passage, correction = row.get(cols["passage"]) or "", " ".join((row.get(cols["correction"]) or "").split())
        if not correction or " ".join(passage.split()) == correction:
            counts["no change"] += 1
            continue
        source_id = (row.get(cols["entry_id"]) or "").strip() if cols["entry_id"] else ""
        store = seen.setdefault(slug, json.loads((OUT_DIR / f"{slug}.json").read_text()) if (OUT_DIR / f"{slug}.json").exists() else {"suggestions": []})
        if source_id and any(s.get("source_id") == source_id for s in store["suggestions"]):
            counts["already imported"] += 1
            continue
        start, para, how = find_paragraph(slug, passage, t)
        if para is None:
            counts["unmatched"] += 1
            print(f"  not matched ({slug} at {int(t)}s, entry {source_id or '?'}): {how}")
            continue
        new = correction
        if how == "prefix":   # only the start of a long paragraph was offered for editing; keep the rest as it is
            new = correction + para[words_span(para, len(passage.split())):]
        if " ".join(new.split()) == " ".join(para.split()):
            counts["no change"] += 1
            continue
        if LINK_RE.search(new) and not LINK_RE.search(para):        # a link that was not in the passage: spam, never a transcript correction
            counts["spam-like"] += 1
            continue
        repeat = next((x for x in store["suggestions"] if x["old"] == para and x["new"] == new and x.get("status") in ("pending", "approved")), None)
        if repeat:                                                  # the same fix sent again (by someone else, or twice): one card, with a count
            repeat["count"] = repeat.get("count", 1) + 1
            repeat.setdefault("source_ids", [repeat.get("source_id")]).append(source_id)
            counts["repeat (merged)"] += 1
            continue
        consent = bool(cols["consent"]) and (row.get(cols["consent"]) or "").strip().lower() not in NO_WORDS
        name = " ".join((row.get(h) or "").strip() for h in cols["name"]).strip()
        note = EMAIL_RE.sub("[email removed]", (row.get(cols["note"]) or "").strip())[:600] if cols["note"] else ""
        store["suggestions"].append({
            "sid": hashlib.sha1(f"{source_id}|{slug}|{int(t)}|{new}".encode()).hexdigest()[:10], "slug": slug,
            "t": t, "old": para, "new": new, "by": name if (consent and name) else "", "credit": bool(consent and name),
            "note": note, "submitted": (row.get(cols["date"]) or "").strip() if cols["date"] else "", "source_id": source_id,
            "match": how, "status": "pending", "count": 1,
            "flag": ("large change: " + str(round(100 * (1 - difflib.SequenceMatcher(None, para, new).ratio()))) + "% of the paragraph differs")
                    if difflib.SequenceMatcher(None, para, new).ratio() < 0.6 else ""})
        counts["imported"] += 1
    if not dry:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for slug, store in seen.items():
            (OUT_DIR / f"{slug}.json").write_text(json.dumps(store, indent=2, ensure_ascii=False))
    print(("DRY RUN, nothing written: " if dry else "") + ", ".join(f"{n} {k}" for k, n in counts.items() if n) or "nothing to import")


if __name__ == "__main__":
    main()
