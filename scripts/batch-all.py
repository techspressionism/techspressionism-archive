#!/usr/bin/env python3
"""Batch driver -- runs Stages 1, 2, (3 only if needed), 4, 5 across the
full known Salon corpus (raw/salon_video_index.json), unattended.

Designed to survive being left alone: every per-session, per-stage step is
wrapped so one session's failure doesn't halt the run. Failures are logged
to review/parse-failures.csv (per spec's repo structure) rather than
raised. Progress is printed continuously so raw/batch_all.log (redirected
by the caller) shows exactly how far it got if interrupted.

Whisper (Stage 3) only runs for a session if Stage 2 couldn't resolve a
source at all, or flagged the resolved source low-confidence -- and even
then it's isolated in its own subprocess (via .venv, since mlx-whisper
isn't in the main interpreter) with a timeout, so a hang or crash there
can't take down the rest of the batch. Stage 3 is unvalidated; expect this
path to need attention afterward if it triggers.

Usage:
    python3 scripts/batch-all.py
"""
import csv
import importlib.util
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
VIDEO_JSON_DIR = REPO_ROOT / "raw" / "video_json"
INDEX_PATH = REPO_ROOT / "raw" / "salon_video_index.json"
SESSIONS_PATH = REPO_ROOT / "data" / "sessions.json"
ARTISTS_PATH = REPO_ROOT / "data" / "artists.json"
VOCAB_PATH = REPO_ROOT / "data" / "vocabulary.json"
INVENTORY_PATH = REPO_ROOT / "data" / "local-video-inventory.json"
FAILURES_CSV = REPO_ROOT / "review" / "parse-failures.csv"
WHISPER_TIMEOUT_SECONDS = 3600  # 1hr ceiling per session -- long recordings exist


def load_stage_module(filename):
    path = ROOT / filename
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def record_failure(failures, number, stage, error):
    failures.append({"number": number, "stage": stage, "error": str(error)})
    log(f"  Salon {number}: {stage} FAILED -- {error}")


def fetch_video_json(number, video_id, failures):
    out_path = VIDEO_JSON_DIR / f"{video_id}.json"
    if out_path.exists():
        return out_path
    try:
        VIDEO_JSON_DIR.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--skip-download", f"https://www.youtube.com/watch?v={video_id}"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0 or not result.stdout.strip():
            record_failure(failures, number, "fetch_video_json", result.stderr[-300:] or "empty output")
            return None
        out_path.write_text(result.stdout)
        return out_path
    except Exception as e:
        record_failure(failures, number, "fetch_video_json", e)
        return None


def maybe_run_whisper(session, failures):
    number = session["number"]
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        record_failure(failures, number, "whisper", "no .venv found, skipping Whisper fallback")
        return
    log(f"  Salon {number}: no usable Stage 2 source, attempting Whisper fallback (this may take a while)...")
    try:
        result = subprocess.run(
            [str(venv_python), str(ROOT / "03-whisper-transcribe.py"), str(number)],
            capture_output=True, text=True, timeout=WHISPER_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            record_failure(failures, number, "whisper", result.stderr[-500:] or "non-zero exit")
        else:
            log(f"  Salon {number}: Whisper fallback completed")
    except subprocess.TimeoutExpired:
        record_failure(failures, number, "whisper", f"timed out after {WHISPER_TIMEOUT_SECONDS}s")
    except Exception as e:
        record_failure(failures, number, "whisper", e)


def main():
    stage1 = load_stage_module("01-harvest.py")
    stage2 = load_stage_module("02-transcripts.py")
    stage4 = load_stage_module("04-correct-names.py")
    stage5 = load_stage_module("05-build-corpus.py")

    with open(INDEX_PATH) as f:
        video_index = json.load(f)
    with open(ARTISTS_PATH) as f:
        artists = json.load(f)
    with open(VOCAB_PATH) as f:
        vocab_terms = json.load(f)["terms"]
    with open(INVENTORY_PATH) as f:
        inventory = json.load(f)
    overrides = stage1.load_overrides()

    failures = []
    total = len(video_index)
    log(f"Starting batch over {total} known Salon sessions")

    # ---- Stage 1: metadata harvest ----
    sessions = []
    for i, (number, entry) in enumerate(sorted(video_index.items(), key=lambda kv: int(kv[0])), 1):
        video_json_path = fetch_video_json(number, entry["video_id"], failures)
        if not video_json_path:
            continue
        try:
            session = stage1.harvest_one(video_json_path)
            stage1.apply_overrides(session, overrides)
            sessions.append(session)
        except Exception as e:
            record_failure(failures, number, "stage1_harvest", f"{e}\n{traceback.format_exc()[-300:]}")
        if i % 10 == 0 or i == total:
            log(f"Stage 1: {i}/{total} processed")

    sessions.sort(key=lambda s: (s["number"] is None, s["number"]))
    with open(SESSIONS_PATH, "w") as f:
        json.dump(sessions, f, indent=2, ensure_ascii=False)
    log(f"Stage 1 done: {len(sessions)} sessions written to {SESSIONS_PATH}")

    # ---- Stage 2: transcript source selection (+ Whisper fallback if needed) ----
    for i, session in enumerate(sessions, 1):
        number = session["number"]
        try:
            source, quality, _ = stage2.process_session(session, inventory)
            if source == "none" or quality.get("low_confidence_flag"):
                maybe_run_whisper(session, failures)
                # re-check what Whisper produced, if anything, so Stage 4/5 see it
                try:
                    source, quality, _ = stage2.process_session(session, inventory)
                except Exception:
                    pass
        except Exception as e:
            record_failure(failures, number, "stage2_transcript", f"{e}\n{traceback.format_exc()[-300:]}")
        if i % 10 == 0 or i == len(sessions):
            log(f"Stage 2: {i}/{len(sessions)} processed")

    with open(SESSIONS_PATH, "w") as f:
        json.dump(sessions, f, indent=2, ensure_ascii=False)

    # ---- Stage 4: name correction ----
    review_rows = []
    for i, session in enumerate(sessions, 1):
        try:
            stage4.process_session(session, artists, vocab_terms, review_rows)
        except Exception as e:
            record_failure(failures, session["number"], "stage4_names", f"{e}\n{traceback.format_exc()[-300:]}")
        if i % 10 == 0 or i == len(sessions):
            log(f"Stage 4: {i}/{len(sessions)} processed")

    if review_rows:
        review_csv = REPO_ROOT / "review" / "name-candidates.csv"
        review_csv.parent.mkdir(parents=True, exist_ok=True)
        file_exists = review_csv.exists()
        with open(review_csv, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["session", "candidate_name", "matched_text", "similarity", "context", "cue_start"])
            if not file_exists:
                writer.writeheader()
            writer.writerows(review_rows)
        log(f"Stage 4: {len(review_rows)} candidates added to review/name-candidates.csv")

    # ---- Stage 5: corpus build ----
    corpus_review_rows = []
    built = 0
    for i, session in enumerate(sessions, 1):
        try:
            result = stage5.process_session(session, artists, vocab_terms, corpus_review_rows)
            if result:
                built += 1
        except Exception as e:
            record_failure(failures, session["number"], "stage5_corpus", f"{e}\n{traceback.format_exc()[-300:]}")
        if i % 10 == 0 or i == len(sessions):
            log(f"Stage 5: {i}/{len(sessions)} processed")

    if corpus_review_rows:
        review_csv = REPO_ROOT / "review" / "name-candidates.csv"
        with open(review_csv, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["session", "candidate_name", "matched_text", "similarity", "context", "cue_start"])
            writer.writerows(corpus_review_rows)

    log(f"Stage 5 done: {built}/{len(sessions)} corpus files built")

    # ---- failure log ----
    if failures:
        FAILURES_CSV.parent.mkdir(parents=True, exist_ok=True)
        with open(FAILURES_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["number", "stage", "error"])
            writer.writeheader()
            writer.writerows(failures)
        log(f"{len(failures)} failures logged to {FAILURES_CSV}")
    else:
        log("No failures.")

    log("Batch complete.")


if __name__ == "__main__":
    main()
