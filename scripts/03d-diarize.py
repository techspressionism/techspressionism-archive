#!/usr/bin/env python3
"""Stage 3d -- separate the voices in ONE recording and name them.

1. pyannote (Hugging Face; needs the free account, the accepted model terms and
   `hf auth login` -- see below) listens to the recording's audio and splits it
   into anonymous voices: V1 spoke here, V2 here, V1 again. It knows no names.
2. Each voice is named by voting over the on-screen names Stage 3c (Nametag) read
   while that voice was talking. A voice is named only when the vote is decisive
   AND informative: if the same name is showing no matter who talks (a pinned or
   spotlighted camera), the vote says nothing about the voice and is rejected.
3. A voice that cannot be named stays unnamed and its words stay "Unattributed";
   the archive never prints "Speaker 2".

Nothing that identifies a voice across recordings is stored: only who spoke when
inside this recording. Everything runs on this Mac; only the model is downloaded.

    .venv-diar/bin/python scripts/03d-diarize.py salon-092              # diarize + name
    .venv-diar/bin/python scripts/03d-diarize.py --rename salon-092     # redo naming only (no model)
    .venv-diar/bin/python scripts/03d-diarize.py --score salon-092      # also score against Zoom's labels

One-time setup (Colin does the account steps; nothing here handles a token):
    huggingface.co account -> accept the terms on pyannote/speaker-diarization-community-1
    -> create a READ token -> run `hf auth login` on this Mac and paste it there.
The pipeline is installed in its own venv, .venv-diar, so it cannot disturb Whisper's.

Writes raw/diarize/<slug>.json with "approved": false. Nothing uses it until it has
been checked and set to true (Stage 5 does not read it yet).
"""
import bisect
import importlib.util
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from lib_media import label, selected, slug
from lib_speakers import canonical_name

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_DIR = ROOT / "raw" / "diarize"
NAMETAG_DIR = ROOT / "raw" / "nametag"
AUDIO_DIR = ROOT / "raw" / "audio"
PIPELINE = "pyannote/speaker-diarization-community-1"

LAG = 1.0          # seconds: the on-screen name changes about a second after a voice starts
MIN_VOTES = 8      # name samples (2 s each) a voice needs before it can be named
MIN_SHARE = 0.75   # the winning name must have at least this share of the voice's votes...
MIN_LIFT = 0.25    # ...and beat that name's share over the whole recording by this much

# ffmpeg and yt-dlp live in the Whisper venv
os.environ["PATH"] = f"{ROOT / '.venv' / 'bin'}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"


def _load(filename):
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_").replace(".py", ""), HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- naming (no model needed)

def merge_spellings(names):
    """name -> the most common spelling of the same person (OCR near-misses, spacing)."""
    spellings, out = [], {}
    for name, _ in Counter(names).most_common():
        key = re.sub(r"[^a-z]", "", name.lower())
        hit = next((s for s in spellings if SequenceMatcher(None, key, s[0]).ratio() >= 0.85), None)
        if not hit:
            spellings.append((key, name))
            hit = spellings[-1]
        out[name] = hit[1]
    return out


def name_voices(turns, samples, clean):
    """turns: [(start, end, voice)]; samples: [[t, raw on-screen text or None, layout]];
    clean: text -> person name or None. Returns {voice: {"seconds", "votes", "name", "why"}}."""
    turns = sorted(turns)
    starts = [t[0] for t in turns]
    names = [(t, clean(n)) for t, n, _ in samples]
    spelling = merge_spellings([n for _, n in names if n])
    votes, overall = defaultdict(Counter), Counter()
    for t, n in names:
        if not n:
            continue
        i = bisect.bisect_right(starts, t - LAG) - 1
        if i < 0 or turns[i][1] < t - LAG:      # nobody speaking then
            continue
        votes[turns[i][2]][spelling[n]] += 1
        overall[spelling[n]] += 1
    total = sum(overall.values()) or 1
    seconds = Counter()
    for s, e, v in turns:
        seconds[v] += e - s
    result = {}
    for voice, secs in seconds.items():
        v = votes.get(voice, Counter())
        n_votes = sum(v.values())
        entry = {"seconds": round(secs, 1), "votes": dict(v.most_common(5)), "name": None}
        if n_votes < MIN_VOTES:
            entry["why"] = f"only {n_votes} name readings while this voice spoke"
        else:
            top, count = v.most_common(1)[0]
            share, lift = count / n_votes, count / n_votes - overall[top] / total
            if share < MIN_SHARE:
                entry["why"] = f"the on-screen name varied ({top} only {share:.0%})"
            elif lift < MIN_LIFT:
                entry["why"] = f"{top} is on screen {overall[top] / total:.0%} of the time whoever speaks (pinned camera?)"
            else:
                entry["name"], entry["why"] = top, f"{share:.0%} of {n_votes} readings, vs {overall[top] / total:.0%} overall"
        result[voice] = entry
    return result


def name_by_index(turns, index, duration):
    """Second, independent source of names: the speaker index (name + start time) from the
    YouTube description / techspressionism.com page. A block runs from one entry's start to
    the next; the voice that speaks most of it is that person's, if it clearly dominates."""
    blocks = [(index[i][1], index[i][0], index[i + 1][0] if i + 1 < len(index) else duration)
              for i in range(len(index))]
    votes = defaultdict(Counter)
    for name, a, b in blocks:
        seconds = Counter()
        for s, e, v in turns:
            overlap = min(e, b) - max(s, a)
            if overlap > 0:
                seconds[v] += overlap
        total = sum(seconds.values())
        if total:
            voice, top = seconds.most_common(1)[0]
            if top / total >= 0.5:
                votes[voice][name] += top
    return {v: c.most_common(1)[0][0] for v, c in votes.items()
            if c.most_common(1)[0][1] / sum(c.values()) >= 0.6}


def combine(voices, index_names, canonical):
    """Two tiers. A name is 'confirmed' only when the on-screen names and the speaker index BOTH
    name the voice and agree (tested on seven Salons: 18 of 18 right; either source alone was
    right 87-92% of the time). One source alone is only a 'single' candidate for review; a
    disagreement is a 'conflict'. Only confirmed voices get a name; the rest stay Unattributed."""
    norm = lambda n: re.sub(r"[^a-z]", "", canonical(n or "").lower())
    for voice, info in voices.items():
        screen, listed = info["name"], index_names.get(voice)
        info["screen"], info["index"] = screen, listed
        if screen and listed:
            if SequenceMatcher(None, norm(screen), norm(listed)).ratio() >= 0.75:
                info["tier"], info["name"] = "confirmed", listed   # the index's spelling is cleaner
            else:
                info["tier"], info["name"] = "conflict", None
                info["why"] = f"screen says {screen!r}, the index says {listed!r}"
        elif screen or listed:
            info["tier"], info["name"], info["candidate"] = "single", None, screen or listed
            info["why"] = f"only the {'screen' if screen else 'speaker index'} names it ({screen or listed})"
        else:
            info["tier"], info["name"] = "none", None
    return voices


# ---------------------------------------------------------------- the model

def audio_for(stage3, session):
    """16 kHz mono wav of the recording (same source rules as Stage 3)."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    wav = AUDIO_DIR / f"{slug(session)}_16k.wav"
    if wav.exists():
        return wav, False
    local = stage3.find_local_audio(session)
    if local:
        stage3.resample_to_whisper_wav(local, wav)
    else:
        raw = stage3.extract_audio_via_ytdlp(session["video_id"], AUDIO_DIR)
        stage3.resample_to_whisper_wav(raw, wav)
        raw.unlink()
    return wav, True


def diarize(wav):
    """[(start, end, 'V1'..)] with voices numbered by how much they speak."""
    try:
        import torch
        from pyannote.audio import Pipeline
    except ImportError:
        sys.exit("pyannote.audio is not installed in this interpreter -- run this with .venv-diar/bin/python")
    try:
        pipe = Pipeline.from_pretrained(PIPELINE)   # uses the login saved by `hf auth login`
    except Exception as e:
        sys.exit(f"could not load {PIPELINE}: {e}\n"
                 "Have you accepted the model's terms on huggingface.co and run `hf auth login` on this Mac?")
    if torch.backends.mps.is_available():
        try:
            pipe.to(torch.device("mps"))
        except Exception:
            pass
    # hand it the samples directly (our 16 kHz mono wav) so it needs no ffmpeg libraries of its own
    from scipy.io import wavfile
    rate, data = wavfile.read(str(wav))
    waveform = torch.from_numpy(data.astype("float32") / 32768.0).unsqueeze(0)
    out = pipe({"waveform": waveform, "sample_rate": rate})
    annotation = getattr(out, "speaker_diarization", out)   # pyannote 4 returns a wrapper object
    raw = [(seg.start, seg.end, spk) for seg, _, spk in annotation.itertracks(yield_label=True)]
    seconds = Counter()
    for s, e, spk in raw:
        seconds[spk] += e - s
    rename = {spk: f"V{i + 1}" for i, (spk, _) in enumerate(seconds.most_common())}
    return [(round(s, 2), round(e, 2), rename[spk]) for s, e, spk in raw]


# ---------------------------------------------------------------- scoring against Zoom's labels

def score(turns, voices, session):
    path = ROOT / "raw" / "transcripts" / f"{slug(session)}.json"
    src = json.loads(path.read_text()) if path.exists() else {}
    if src.get("source") != "zoom-transcript":
        print("  (no Zoom transcript for this recording -- nothing to score against)")
        return
    cues = src["cues"]
    zoom_at = lambda t: next((c["speaker"] for c in cues if c["start"] <= t <= (c.get("end") or c["start"] + 2)), None)
    overlap = defaultdict(Counter)              # voice -> Zoom speaker -> seconds
    for s, e, v in turns:
        t = s
        while t < e:
            z = zoom_at(t)
            if z:
                overlap[v][z] += 1.0
            t += 1.0
    right = total = 0.0
    for v, c in overlap.items():
        right += c.most_common(1)[0][1]
        total += sum(c.values())
    print(f"  voice purity vs Zoom's labels: {100 * right / max(total, 1):.0f}% of speech time "
          f"(each voice matched to the Zoom speaker it overlaps most)")
    key = lambda n: re.sub(r"[^a-z]", "", (n or "").lower())
    named = [(v, voices[v]["name"], overlap[v].most_common(1)[0][0]) for v in voices if voices[v]["name"] and overlap[v]]
    ok = sum(1 for _, n, z in named if SequenceMatcher(None, key(n), key(z)).ratio() >= 0.8)
    print(f"  voices named: {len(named)} of {len(voices)}; named correctly (vs Zoom): {ok} of {len(named)}")
    for v, n, z in named:
        print(f"    {v}: named {n!r}, Zoom says {z!r}")


# ---------------------------------------------------------------- main

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        sys.exit(__doc__)
    sessions = [s for s in json.load(open(ROOT / "data" / "sessions.json")) if selected(s, args)]
    if not sessions:
        sys.exit(f"nothing matches {args}")
    stage3 = None if "--rename" in flags else _load("03-whisper-transcribe.py")
    clean = _load("05-build-corpus.py")._speaker_name
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for s in sessions:
        print(f"{label(s)}:", flush=True)
        out_path = OUT_DIR / f"{slug(s)}.json"
        started = time.time()
        if "--rename" in flags:
            turns = [tuple(t) for t in json.loads(out_path.read_text())["turns"]]
        else:
            wav, made = audio_for(stage3, s)
            turns = diarize(wav)
            if made and "--keep-audio" not in flags:
                wav.unlink(missing_ok=True)
        tag = NAMETAG_DIR / f"{slug(s)}.json"
        samples = json.loads(tag.read_text())["samples"] if tag.exists() else []
        voices = name_voices(turns, samples, clean) if samples else {
            v: {"seconds": round(sec, 1), "votes": {}, "name": None, "why": "no Nametag readings for this recording"}
            for v, sec in Counter({t[2]: 0 for t in turns}).items()}
        index = sorted((x["start_seconds"], x["name"]) for x in s.get("speakers", []) if x.get("start_seconds") is not None)
        voices = combine(voices, name_by_index(turns, index, s["duration_seconds"]) if index else {}, canonical_name)
        out_path.write_text(json.dumps({"video_id": s["video_id"], "pipeline": PIPELINE, "approved": False,
                                        "turns": turns, "voices": voices}, ensure_ascii=False))
        all_secs = sum(v["seconds"] for v in voices.values()) or 1
        tiers = Counter(v["tier"] for v in voices.values())
        cover = lambda t: 100 * sum(v["seconds"] for v in voices.values() if v["tier"] == t) / all_secs
        print(f"  {len(voices)} voices; confirmed {tiers['confirmed']} ({cover('confirmed'):.0f}% of speech), "
              f"single-source {tiers['single']} ({cover('single'):.0f}%), conflict {tiers['conflict']}, "
              f"unnamed {tiers['none']}; {(time.time() - started) / 60:.1f} min", flush=True)
        for v, info in sorted(voices.items(), key=lambda kv: -kv[1]["seconds"])[:12]:
            print(f"    {v} {info['seconds'] / 60:5.1f} min  {info['tier']:9} {info['name'] or info.get('candidate') or '':24} {info.get('why', '')}")
        if "--score" in flags:
            score(turns, voices, s)


if __name__ == "__main__":
    main()
