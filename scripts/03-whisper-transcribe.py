#!/usr/bin/env python3
"""Stage 3 -- Whisper transcription, for recordings where neither a local
Zoom transcript nor YouTube captions exist, or where Whisper is wanted for
better accuracy and native punctuation. Runs on mlx-whisper, which requires
the project virtualenv:
    .venv/bin/python scripts/03-whisper-transcribe.py interview-015
    .venv/bin/python scripts/03-whisper-transcribe.py 48 salon-049   # bare number = a Salon

    --force        redo recordings that already have raw/whisper/<slug>.json
                   (default: skip them, so a long batch can be resumed)
    --keep-audio   keep the 16 kHz audio in raw/audio/ (default: delete it once
                   transcribed -- it is ~115 MB per hour)

Output goes to raw/whisper/<slug>.json (NOT raw/transcripts/, so re-running
Stage 2 can't clobber it). Stage 2 then prefers it over YouTube captions when
no Zoom transcript exists, and Stage 5 treats it like human subtitles: Whisper
already punctuates and capitalizes, and per-word timestamps drive the same
speaker-index slicing and ~3-minute block splitting as the YouTube path. Whisper
gives no speaker labels.

Audio source: the original recording in ~/Documents/~TECHSPRESSIONISM/VIDEO when
one can be found (Salon N -> SALON/SALON_N, Roundtable N -> ROUNDTABLE/ROUNDTABLE_N,
Interview -> INTERVIEWS/<first name>). Those folders also hold promos and edits, so
a file only counts if its duration is within 1% of the YouTube video's; .m4a is
preferred, then .mp4/.mov/.mp3. Otherwise the audio comes from YouTube via yt-dlp.

Safety check: a wrong-but-similar-length file would transcribe the wrong
recording without any error. So when YouTube captions exist, the Whisper text
is compared against them (share of word pairs in common). Below
CROSSCHECK_MIN_OVERLAP the result goes to raw/whisper/rejected/ and is NOT used.

Known gap: the spec calls for per-segment language detection ("at least
one Salon (#48) includes French-language presentation... preserve the
original language transcript alongside any translation"). mlx_whisper's
transcribe() detects language once for the whole file, not per segment, so
this only produces a single-language transcript in whatever Whisper detects
for the file as a whole. Revisit when a real multilingual recording forces
the issue.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from lib_media import label, media_type, selected, slug

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_PATH = ROOT / "data" / "sessions.json"
CAPTIONS_DIR = ROOT / "raw" / "captions"
AUDIO_CACHE_DIR = ROOT / "raw" / "audio"
OUT_DIR = ROOT / "raw" / "whisper"
REJECTED_DIR = OUT_DIR / "rejected"
LOCAL_VIDEO_DIR = Path.home() / "Documents" / "~TECHSPRESSIONISM" / "VIDEO"
LOCAL_FOLDERS = {"salon": "SALON", "interview": "INTERVIEWS", "roundtable": "ROUNDTABLE"}
MEDIA_EXTS = [".m4a", ".mp4", ".mov", ".mp3"]  # in order of preference
DURATION_TOLERANCE = 0.01  # fraction of the video's length, plus 2 s

MODEL_REPO = "mlx-community/whisper-large-v3-mlx"
BRACKET_MARKER_RE = re.compile(r"\[[^\]]+\]")
CROSSCHECK_MIN_OVERLAP = 0.35

# yt-dlp and ffmpeg live in the venv's bin/; make them findable without
# requiring `source .venv/bin/activate` (batch-all.py invokes this by full path).
# Set process-wide: mlx_whisper shells out to ffmpeg itself when loading audio.
os.environ["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"


def probe_duration(path):
    """Seconds, read from the file header (ffmpeg -i decodes nothing)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


def local_search_roots(session):
    top = LOCAL_FOLDERS.get(media_type(session))
    if not top:
        return []
    base, n = LOCAL_VIDEO_DIR / top, int(session["number"])
    kind = media_type(session)
    if kind == "salon":
        return [base / f"SALON_{n}"]
    if kind == "roundtable":
        return [base / f"ROUNDTABLE_{n}"]
    # interview folders are named by first name ("DARCY", "CARI ANN")
    name = (session.get("interviewee") or "").upper()
    matches = [d for d in sorted(base.iterdir()) if d.is_dir() and name.startswith(d.name.upper())] if base.is_dir() else []
    # Only the interviewee's OWN folder is searched. (It used to fall back to the whole INTERVIEWS folder when there was no such folder, and then took ANY
    # recording of about the right length: on 21 September 2026 that put Interview 13's audio into Interview 30.) No folder: the audio comes from YouTube.
    return matches


def find_local_audio(session):
    """The original recording, or None. See the module docstring for the rules."""
    duration = session.get("duration_seconds")
    if not duration:
        return None
    found = []
    for root in local_search_roots(session):
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.name.startswith(".") or p.suffix.lower() not in MEDIA_EXTS or "Premiere" in str(p):
                continue
            d = probe_duration(p)
            if d and abs(d - duration) <= DURATION_TOLERANCE * duration + 2:
                found.append((MEDIA_EXTS.index(p.suffix.lower()), "gallery" in p.name.lower(), str(p)))
    return Path(sorted(found)[0][2]) if found else None


def extract_audio_via_ytdlp(video_id, dest_dir):
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp", *(["--js-runtimes", "node"] if shutil.which("node") else []),
        "--http-chunk-size", "10M", "--retries", "10", "--fragment-retries", "10",     # YouTube throttles one long request; short ones keep speed
        "-f", "bestaudio", "-x", "--audio-format", "wav", "--audio-quality", "0",
        "-o", str(dest_dir / f"{video_id}.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    for wait in (30, 90, 240):          # YouTube sometimes answers a download with a passing 403 or 429: wait and try again
        if r.returncode == 0 or not re.search(r"HTTP Error (403|429|5\d\d)", (r.stderr or "") + (r.stdout or "")):
            break
        time.sleep(wait)
        r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:  # say WHY (rate limit, sign-in, unavailable...) instead of just the command line
        raise RuntimeError(f"yt-dlp failed for {video_id}: {(r.stderr or r.stdout).strip().splitlines()[-1][:300] if (r.stderr or r.stdout).strip() else 'no output'}")
    candidates = list(dest_dir.glob(f"{video_id}.wav"))
    return candidates[0] if candidates else None


def resample_to_whisper_wav(src_path, dest_path):
    """16kHz mono PCM16 -- what Whisper wants; anything else wastes time resampling."""
    cmd = ["ffmpeg", "-y", "-i", str(src_path), "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dest_path)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def compute_quality(cues, duration_seconds):
    full_text = " ".join(c["text"] for c in cues)
    word_count = len(full_text.split())
    duration_min = (duration_seconds or 0) / 60 or 1
    wpm = round(word_count / duration_min, 1)
    marker_count = len(BRACKET_MARKER_RE.findall(full_text))
    low_confidence = wpm < 60 or (word_count and marker_count / max(word_count, 1) > 0.05)
    return {
        "word_count": word_count, "duration_seconds": duration_seconds,
        "words_per_minute": wpm, "bracket_marker_count": marker_count,
        "low_confidence_flag": bool(low_confidence),
    }


def _bigrams(words):
    return {(a, b) for a, b in zip(words, words[1:])}


def caption_words(session):
    """Lowercased words from the recording's YouTube caption file, or None."""
    d = CAPTIONS_DIR / slug(session)
    vtts = sorted(d.glob("*.vtt")) if d.is_dir() else []
    if not vtts:
        return None
    lines = [l for l in vtts[0].read_text(errors="replace").splitlines()
             if "-->" not in l and not l.startswith(("WEBVTT", "Kind:", "Language:"))]
    return re.findall(r"[a-z']+", re.sub(r"<[^>]*>", " ", " ".join(lines)).lower())


def crosscheck(session, whisper_words):
    """Share of the smaller transcript's word pairs found in the other. Same
    recording, two engines: well above the threshold. Different recording: near 0.
    None when there are no YouTube captions to compare against."""
    reference = caption_words(session)
    if not reference:
        return None
    ours = re.findall(r"[a-z']+", " ".join(w["text"] for w in whisper_words).lower())
    a, b = _bigrams(ours), _bigrams(reference)
    return round(len(a & b) / min(len(a), len(b)), 3) if a and b else None


def transcribe_session(session, keep_audio):
    try:
        import mlx_whisper
    except ImportError:
        print("mlx-whisper not installed in this interpreter -- run via .venv/bin/python", file=sys.stderr)
        sys.exit(1)

    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    wav_path = AUDIO_CACHE_DIR / f"{slug(session)}_16k.wav"

    audio_source = "cached"
    if not wav_path.exists():
        local_audio = find_local_audio(session)
        if local_audio:
            print(f"  using local recording: {local_audio}")
            resample_to_whisper_wav(local_audio, wav_path)
            audio_source = f"local:{local_audio}"
        else:
            print(f"  no matching local recording, extracting from YouTube ({session['video_id']})")
            raw_wav = extract_audio_via_ytdlp(session["video_id"], AUDIO_CACHE_DIR)
            if not raw_wav:
                raise RuntimeError(f"Could not extract audio for {label(session)}")
            resample_to_whisper_wav(raw_wav, wav_path)
            raw_wav.unlink()  # ~700 MB per hour, and only wanted for the resample
            audio_source = "youtube"

    print(f"  transcribing with {MODEL_REPO}...")
    result = mlx_whisper.transcribe(str(wav_path), path_or_hf_repo=MODEL_REPO, word_timestamps=True, verbose=False)

    cues = [
        {"start": round(seg["start"], 2), "end": round(seg["end"], 2), "speaker": None, "text": seg["text"].strip()}
        for seg in result["segments"]
        if seg["text"].strip()
    ]
    # per-word timing, same shape Stage 2 builds for YouTube captions.
    # Whisper marks the start of each real word with a leading space; a token
    # without one ("-scale", ".com") belongs to the previous word
    words = []
    for seg in result["segments"]:
        for w in seg.get("words", []):
            text = w["word"].strip()
            if not text:
                continue
            if words and not w["word"][0].isspace():
                words[-1]["text"] += text
            else:
                words.append({"start": round(w["start"], 2), "text": text})
    quality = compute_quality(cues, session["duration_seconds"])
    overlap = crosscheck(session, words)
    accepted = overlap is None or overlap >= CROSSCHECK_MIN_OVERLAP

    out_path = (OUT_DIR if accepted else REJECTED_DIR) / f"{slug(session)}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "video_id": session["video_id"], "source": "whisper-large-v3",
            "detected_language": result.get("language"), "audio_source": audio_source,
            "crosscheck_overlap": overlap, "cues": cues, "quality": quality, "words": words,
        }, f, indent=2, ensure_ascii=False)

    if not keep_audio:
        wav_path.unlink(missing_ok=True)
    return out_path, quality, overlap, accepted


def main():
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage: .venv/bin/python scripts/03-whisper-transcribe.py [--force] [--keep-audio] <slug or salon number> [...]", file=sys.stderr)
        sys.exit(1)

    with open(SESSIONS_PATH) as f:
        sessions = json.load(f)
    matched = [s for s in sessions if selected(s, args)]
    if not matched:
        print(f"nothing in {SESSIONS_PATH} matches {args}", file=sys.stderr)
        sys.exit(1)

    failures = 0
    for session in matched:
        print(f"{label(session)}:", flush=True)
        if (OUT_DIR / f"{slug(session)}.json").exists() and "--force" not in flags:
            print("  already transcribed (use --force to redo)")
            continue
        started = time.time()
        try:
            out_path, quality, overlap, accepted = transcribe_session(session, "--keep-audio" in flags)
        except Exception as e:  # keep a long batch going past one bad recording
            failures += 1
            print(f"  FAILED: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            continue
        verdict = "no captions to compare" if overlap is None else f"caption overlap {overlap}"
        print(f"  wrote {out_path} ({quality['word_count']} words, {quality['words_per_minute']} wpm, "
              f"{verdict}, {(time.time() - started) / 60:.1f} min)", flush=True)
        if not accepted:
            print(f"  REJECTED: overlap {overlap} < {CROSSCHECK_MIN_OVERLAP} -- likely the wrong recording; not used", file=sys.stderr, flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
