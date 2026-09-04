#!/usr/bin/env python3
"""Stage 3 -- Whisper fallback transcription, for sessions where neither a
local Zoom transcript nor YouTube captions exist (or where Stage 2 flags
whatever exists as low-confidence).

SPECULATIVE / UNVALIDATED: written per spec but not yet run against a real
session, because every session in the current 99-video Salon corpus
resolves via Stage 2 already (Zoom transcript or YouTube captions). Built
ahead of need so it's ready when a real gap appears -- a genuinely
caption-less session, a low_confidence_flag from Stage 2, or Interview/
Roundtable/Presentation content once that's in scope. Runs on
mlx-whisper, which requires the project virtualenv:
    .venv/bin/python scripts/03-whisper-transcribe.py <video_id_or_number>

Known gap: the spec calls for per-segment language detection ("at least
one Salon (#48) includes French-language presentation... preserve the
original language transcript alongside any translation"). mlx_whisper's
transcribe() detects language once for the whole file, not per segment.
Since this stage has no real multilingual session to validate against
yet, that per-segment handling is NOT implemented -- this only produces a
single-language transcript in whatever Whisper detects for the file as a
whole. Revisit when a real multilingual session forces the issue.

Audio source: prefers the local Zoom recording's .m4a (already on disk,
no download) over re-fetching from YouTube.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_PATH = ROOT / "data" / "sessions.json"
INVENTORY_PATH = ROOT / "data" / "local-video-inventory.json"
AUDIO_CACHE_DIR = ROOT / "raw" / "audio"
OUT_DIR = ROOT / "raw" / "transcripts"
LOCAL_SALON_DIR = Path.home() / "Documents" / "~TECHSPRESSIONISM" / "VIDEO" / "SALON"

MODEL_REPO = "mlx-community/whisper-large-v3-mlx"
BRACKET_MARKER_RE = re.compile(r"\[[^\]]+\]")


def find_local_audio(number, inventory):
    bucket = inventory.get("sessions", {}).get(str(int(number)))
    if not bucket:
        return None
    m4a_files = [f for f in bucket["files"] if f["ext"] == "m4a"]
    if not m4a_files:
        return None
    # prefer the main recording over a "_gallery_" alternate view, if both exist
    m4a_files.sort(key=lambda f: "gallery" in f["path"].lower())
    return LOCAL_SALON_DIR / m4a_files[0]["path"]


def extract_audio_via_ytdlp(video_id, dest_dir):
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp", "-f", "bestaudio", "-x", "--audio-format", "wav", "--audio-quality", "0",
        "-o", str(dest_dir / f"{video_id}.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    candidates = list(dest_dir.glob(f"{video_id}.wav"))
    return candidates[0] if candidates else None


def resample_to_whisper_wav(src_path, dest_path):
    """16kHz mono PCM16 -- what Whisper wants; anything else wastes time resampling."""
    cmd = ["ffmpeg", "-y", "-i", str(src_path), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dest_path)]
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


def transcribe_session(session, inventory):
    try:
        import mlx_whisper
    except ImportError:
        print("mlx-whisper not installed in this interpreter -- run via .venv/bin/python", file=sys.stderr)
        sys.exit(1)

    number = session["number"]
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    wav_path = AUDIO_CACHE_DIR / f"salon-{int(number):03d}_16k.wav"

    if not wav_path.exists():
        local_audio = find_local_audio(number, inventory)
        if local_audio and local_audio.exists():
            print(f"  using local audio: {local_audio}")
            resample_to_whisper_wav(local_audio, wav_path)
        else:
            print(f"  no local audio found, extracting from YouTube ({session['video_id']})")
            raw_wav = extract_audio_via_ytdlp(session["video_id"], AUDIO_CACHE_DIR)
            if not raw_wav:
                raise RuntimeError(f"Could not extract audio for Salon {number}")
            resample_to_whisper_wav(raw_wav, wav_path)

    print(f"  transcribing with {MODEL_REPO} (this is slow on first run -- downloads the model)...")
    result = mlx_whisper.transcribe(str(wav_path), path_or_hf_repo=MODEL_REPO, word_timestamps=False, verbose=False)

    cues = [
        {"start": round(seg["start"], 2), "end": round(seg["end"], 2), "speaker": None, "text": seg["text"].strip()}
        for seg in result["segments"]
        if seg["text"].strip()
    ]
    quality = compute_quality(cues, session["duration_seconds"])

    out_path = OUT_DIR / f"salon-{int(number):03d}.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "video_id": session["video_id"], "source": "whisper-large-v3",
            "detected_language": result.get("language"), "cues": cues, "quality": quality,
        }, f, indent=2, ensure_ascii=False)

    return out_path, quality


def main():
    if len(sys.argv) < 2:
        print("Usage: .venv/bin/python scripts/03-whisper-transcribe.py <salon_number> [<salon_number> ...]", file=sys.stderr)
        sys.exit(1)

    with open(SESSIONS_PATH) as f:
        sessions = {str(s["number"]): s for s in json.load(f)}
    with open(INVENTORY_PATH) as f:
        inventory = json.load(f)

    for number in sys.argv[1:]:
        session = sessions.get(number)
        if not session:
            print(f"Salon {number}: not found in {SESSIONS_PATH}", file=sys.stderr)
            continue
        print(f"Salon {number}:")
        out_path, quality = transcribe_session(session, inventory)
        print(f"  wrote {out_path} ({quality['word_count']} words, {quality['words_per_minute']} wpm)")


if __name__ == "__main__":
    main()
