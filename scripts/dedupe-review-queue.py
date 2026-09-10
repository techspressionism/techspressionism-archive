#!/usr/bin/env python3
"""Collapse review/name-candidates.csv into unique (matched_text, candidate)
patterns with an occurrence count, instead of one row per hit. The raw
queue has massive structural duplication -- the same short/common-word
collision (e.g. "and you" ~ "Andy Young") repeats hundreds of times across
sessions, and reviewing it once is exactly as informative as reviewing it
600 times. High-count rows are near-certainly noise (a real name mention
doesn't recur identically hundreds of times); low-count rows are where the
actual judgment calls live.

Usage:
    python3 scripts/dedupe-review-queue.py
"""
import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IN_PATH = ROOT / "review" / "name-candidates.csv"       # raw, one row per hit (Stage 4/5 output)
OUT_PATH = ROOT / "review" / "name-candidates-summary.csv"  # collapsed, one row per pattern -- work from this

LIKELY_NOISE_THRESHOLD = 10  # a genuine name mention doesn't recur identically this often


def main():
    with open(IN_PATH) as f:
        rows = list(csv.DictReader(f))

    groups = defaultdict(list)
    for r in rows:
        key = (r["matched_text"].strip().lower(), r["candidate_name"])
        groups[key].append(r)

    summary = []
    for (matched_text, candidate), group_rows in groups.items():
        sessions = sorted(set(r["session"] for r in group_rows), key=lambda s: int(s))
        best = max(group_rows, key=lambda r: float(r["similarity"]))
        summary.append({
            "count": len(group_rows),
            "matched_text": group_rows[0]["matched_text"],  # original casing
            "candidate_name": candidate,
            "similarity": best["similarity"],
            "sessions": ";".join(sessions),
            "session_count": len(sessions),
            "example_context": best["context"],
            "likely_noise": "yes" if len(group_rows) >= LIKELY_NOISE_THRESHOLD else "",
        })

    summary.sort(key=lambda s: -s["count"])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["count", "matched_text", "candidate_name", "similarity", "session_count", "sessions", "likely_noise", "example_context"])
        writer.writeheader()
        writer.writerows(summary)

    noise_rows = sum(1 for s in summary if s["likely_noise"])
    noise_events = sum(s["count"] for s in summary if s["likely_noise"])
    print(f"{len(rows)} raw rows -> {len(summary)} unique patterns")
    print(f"{noise_rows} patterns ({noise_events} of the original rows) flagged likely_noise (>= {LIKELY_NOISE_THRESHOLD} identical repeats)")
    print(f"{len(summary) - noise_rows} patterns worth an actual look")
    print(f"\nWrote deduplicated queue to {OUT_PATH}")
    


if __name__ == "__main__":
    main()
