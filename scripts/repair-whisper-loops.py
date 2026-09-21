#!/usr/bin/env python3
"""Repair the stretches where Whisper got stuck in a loop, by transcribing just those stretches again.

    .venv/bin/python scripts/repair-whisper-loops.py salon-057            # one recording
    .venv/bin/python scripts/repair-whisper-loops.py --all                # every Whisper transcript that has loops
    .venv/bin/python scripts/repair-whisper-loops.py interview-019 --full # transcribe the whole recording again with the safer settings

Whisper normally feeds its own previous words back in as context; on hard audio (silence, crosstalk, an accent, a screen share) it then
repeats one phrase again and again, or invents words. This finds each loop (scripts/find-whisper-loops.py), cuts out the loop with 12 s of
audio on each side, transcribes that clip again with condition_on_previous_text OFF and a hallucination guard, and splices the new words
in. Loops that survive are removed (their words dropped) and counted in the report. The original file is kept in raw/whisper/preloop/.
After repairing, run 02-transcripts.py, 04-correct-names.py and 05-build-corpus.py for the recording (this script does not rebuild).
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
_s3 = importlib.util.spec_from_file_location("stage3", ROOT / "scripts" / "03-whisper-transcribe.py")
t3 = importlib.util.module_from_spec(_s3)
_s3.loader.exec_module(t3)
_sf = importlib.util.spec_from_file_location("findloops", ROOT / "scripts" / "find-whisper-loops.py")
fl = importlib.util.module_from_spec(_sf)
_sf.loader.exec_module(fl)
from lib_media import slug as slug_of  # noqa: E402

PAD = 12.0
OPTS = dict(word_timestamps=True, verbose=False, condition_on_previous_text=False, hallucination_silence_threshold=2.0,
            compression_ratio_threshold=2.0, no_speech_threshold=0.5, temperature=(0.0, 0.2, 0.4))


def session_for(slug):
    return next(s for s in json.loads((ROOT / "data" / "sessions.json").read_text()) if slug_of(s) == slug)


def get_wav(session):
    """The 16 kHz audio of the recording (same source rules as Stage 3): its own local recording, else YouTube."""
    wav = t3.AUDIO_CACHE_DIR / f"{slug_of(session)}_16k.wav"
    if wav.exists():
        return wav, False
    t3.AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local = t3.find_local_audio(session)
    if local:
        t3.resample_to_whisper_wav(local, wav)
    else:
        raw = t3.extract_audio_via_ytdlp(session["video_id"], t3.AUDIO_CACHE_DIR)
        t3.resample_to_whisper_wav(raw, wav)
        raw.unlink()
    return wav, True


def words_of(result):
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
    return words


def merge(spans):
    out = []
    for s in sorted(spans, key=lambda x: x["start"]):
        c0, c1 = max(0.0, s["start"] - PAD), s["end"] + PAD
        if out and c0 <= out[-1][1] + 2:
            out[-1][1] = max(out[-1][1], c1)
        else:
            out.append([c0, c1])
    return out


def repair(slug, full=False):
    import mlx_whisper
    path = t3.OUT_DIR / f"{slug}.json"
    d = json.loads(path.read_text())
    session = session_for(slug)
    before = fl.spans(slug)
    if not before and not full:
        return {"slug": slug, "loops_before": 0, "loops_after": 0, "note": "no loops"}
    dur = float(session.get("duration_seconds") or d["words"][-1]["start"] + 10)
    ranges = [[0.0, dur + 5]] if full else merge(before)
    wav, fetched = get_wav(session)
    words, cues = d["words"], d["cues"]
    for c0, c1 in ranges:
        kw = dict(OPTS)
        if not full:
            kw["clip_timestamps"] = [c0, c1]
        res = mlx_whisper.transcribe(str(wav), path_or_hf_repo=t3.MODEL_REPO, **kw)
        new_words = [w for w in words_of(res) if c0 <= w["start"] < c1]
        new_cues = [{"start": round(s["start"], 2), "end": round(s["end"], 2), "speaker": None, "text": s["text"].strip()}
                    for s in res["segments"] if s["text"].strip() and c0 <= s["start"] < c1]
        words = [w for w in words if not (c0 <= w["start"] < c1)] + new_words
        words.sort(key=lambda w: w["start"])
        cues = [c for c in cues if not (c0 <= c["start"] < c1)] + new_cues
        cues.sort(key=lambda c: c["start"])
    # a loop that survived is dropped
    dropped = 0
    for a, b, phrase, reps in reversed(fl.loops(words)):
        del words[a:b + 1]
        dropped += b - a + 1
    if dropped:
        cues = [c for c in cues if not _cue_is_loop(c["text"])]
    d["words"], d["cues"] = words, cues
    d["quality"] = t3.compute_quality(cues, session["duration_seconds"])
    d["repaired_loops"] = {"at": time.strftime("%Y-%m-%d %H:%M"), "ranges": [[round(a), round(b)] for a, b in ranges], "words_dropped": dropped,
                            "mode": "full" if full else "loops only"}
    backup = t3.OUT_DIR / "preloop"
    backup.mkdir(exist_ok=True)
    if not (backup / f"{slug}.json").exists():
        (backup / f"{slug}.json").write_text(path.read_text())
    path.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    after = fl.spans(slug)
    if fetched:
        wav.unlink(missing_ok=True)
    return {"slug": slug, "loops_before": len(before), "loops_after": len(after), "dropped_words": dropped, "ranges": len(ranges)}


def _cue_is_loop(text):
    ws = [fl.norm(w) for w in text.split()]
    return len(ws) >= 8 and len(set(ws)) <= max(2, len(ws) // 6)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full = "--full" in sys.argv
    targets = args
    if "--all" in sys.argv:
        targets = [p.stem for p in sorted(t3.OUT_DIR.glob("*.json")) if fl.spans(p.stem)]
    if not targets:
        sys.exit(__doc__)
    report = []
    for slug in targets:
        t0 = time.time()
        try:
            r = repair(slug, full)
        except Exception as e:                        # one recording failing must not stop the batch
            r = {"slug": slug, "error": f"{type(e).__name__}: {e}"[:200]}
        r["minutes"] = round((time.time() - t0) / 60, 1)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        report.append(r)
    (ROOT / "raw" / "repair-whisper-loops-report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
