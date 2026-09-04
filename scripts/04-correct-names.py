#!/usr/bin/env python3
"""Stage 4 -- name and vocabulary correction.

1. Controlled vocabulary (data/vocabulary.json): literal substitution of
   known ASR mishearings for recurring proper nouns. High confidence by
   construction (curated list), applied unconditionally.

2. Person names: fuzzy sliding-window match of candidate names against
   each transcript's text. Two candidate pools with different thresholds:
     - the session's own speaker index from Stage 1 (data/sessions.json)
       is a strong prior -- these people are known to be in this specific
       session, so a lower match threshold is safe.
     - the full artist index (data/artists.json, 463 people) is a much
       weaker prior -- anyone could be *mentioned* in conversation, so
       auto-correction requires a much higher threshold to avoid false
       positives on short/common name fragments.
   High-confidence matches are corrected in place. Mid-confidence matches
   are written to review/name-candidates.csv with context, NOT corrected
   -- per spec, do not silently guess.

Note: this runs whole-transcript, which is the right shape for Zoom-sourced
sessions (cues already carry per-utterance speaker attribution). For flat,
speaker-less YouTube-sourced sessions, Stage 5 does its own per-segment
correction pass (via lib_corrections) *after* slicing by Stage 1's speaker
index, rather than reusing this stage's output -- slicing a corrected flat
blob back into segments risks desyncing word-level timestamps from text
that Stage 4's substitutions may have changed the length of. This script's
output for those sessions is still useful as a whole-transcript QA pass.

Usage:
    python3 scripts/04-correct-names.py 20 48 109
"""
import csv
import json
import sys
from pathlib import Path

from lib_corrections import correct_text

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_PATH = ROOT / "data" / "sessions.json"
ARTISTS_PATH = ROOT / "data" / "artists.json"
VOCAB_PATH = ROOT / "data" / "vocabulary.json"
TRANSCRIPTS_DIR = ROOT / "raw" / "transcripts"
OUT_DIR = ROOT / "raw" / "transcripts_corrected"
REVIEW_CSV = ROOT / "review" / "name-candidates.csv"


def process_session(session, artists, vocab_terms, review_rows):
    number = session["number"]
    in_path = TRANSCRIPTS_DIR / f"salon-{int(number):03d}.json"
    if not in_path.exists():
        print(f"Salon {number}: no transcript file at {in_path}, skipping")
        return None

    with open(in_path) as f:
        data = json.load(f)

    vocab_subs_total = 0
    corrections = []
    for cue in data["cues"]:
        cue["text"], n = correct_text(cue["text"], session, artists, vocab_terms, number, cue.get("start"), review_rows, corrections)
        vocab_subs_total += n

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"salon-{int(number):03d}.json"
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return {"vocab_substitutions": vocab_subs_total, "name_corrections": corrections, "out_path": out_path}


def main():
    with open(SESSIONS_PATH) as f:
        sessions = json.load(f)
    with open(ARTISTS_PATH) as f:
        artists = json.load(f)
    with open(VOCAB_PATH) as f:
        vocab_terms = json.load(f)["terms"]

    numbers = set(sys.argv[1:]) if len(sys.argv) > 1 else None
    review_rows = []

    for session in sessions:
        if numbers is not None and str(session["number"]) not in numbers:
            continue
        result = process_session(session, artists, vocab_terms, review_rows)
        if result:
            print(f"Salon {session['number']}: {result['vocab_substitutions']} vocab substitutions, "
                  f"{len(result['name_corrections'])} name corrections -> {result['out_path']}")
            for c in result["name_corrections"]:
                print(f"    '{c['original']}' -> '{c['corrected']}' (ratio={c['ratio']}, t={c['cue_start']}s)")

    REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
    file_exists = REVIEW_CSV.exists()
    with open(REVIEW_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["session", "candidate_name", "matched_text", "similarity", "context", "cue_start"])
        if not file_exists:
            writer.writeheader()
        for row in review_rows:
            writer.writerow(row)

    print(f"\n{len(review_rows)} candidates written to {REVIEW_CSV} for manual review")


if __name__ == "__main__":
    main()
