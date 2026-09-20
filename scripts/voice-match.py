#!/usr/bin/env python3
"""Voice match: suggest that a voice in a recording is a person whose own voice has been enrolled (starting with Colin Goldberg).

    .venv-diar/bin/python scripts/voice-match.py enroll  colin-goldberg "Colin Goldberg" salon-081 salon-085 ...
    .venv-diar/bin/python scripts/voice-match.py validate colin-goldberg          # leave-one-recording-out test
    .venv-diar/bin/python scripts/voice-match.py match   colin-goldberg salon-055 salon-054 | --all
    .venv-diar/bin/python scripts/voice-match.py forget  colin-goldberg           # delete the voiceprint and its cache

PRIVACY (a voice signature is biometric data):
  * Only people who have agreed are enrolled: Colin Goldberg first, on his own instruction (20 September 2026).
  * The signature is a list of numbers made on this Mac from audio already on this Mac. It lives in private/voiceprints/ (git-ignored),
    is never committed, published or sent anywhere, and `forget` deletes it. The pages and the public repository never contain it.
  * A match is only a SUGGESTION: it is written into raw/diarize/<slug>.json as a candidate name (tier "single", the reason says
    "voice match ...") which NameReview shows pre-selected. Nothing is used until a person confirms it; Stage 5 ignores candidates.

How it works: the diarization model's own speaker-embedding network turns 4-second stretches of a voice into numbers; a voice is
represented by the average of up to 60 stretches. ENROLL takes, from recordings where Zoom's labels are available, the separated voice that
Zoom's labels say is the person (at least 90% overlap), so the signature comes from checked ground truth across several recordings.
A voice in another recording is suggested only when its similarity to the signature is at least THRESHOLD and beats every other voice in that
recording by GAP; both are set by VALIDATE from recordings where the truth is known and it refuses to suggest anything when the two groups overlap.
"""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STORE = ROOT / "private" / "voiceprints"
WINDOW = 4.0
MAX_WINDOWS = 60


def load_03d():
    spec = importlib.util.spec_from_file_location("d03", HERE / "03d-diarize.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    spec.loader.exec_module(mod)
    return mod


def get_embedder():
    import torch
    from pyannote.audio import Pipeline
    pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1")
    try:
        pipe.to(torch.device("cpu"))
    except Exception:
        pass
    return pipe._embedding, torch      # the speaker-embedding network inside the diarization pipeline


def voice_embedding(emb, torch, audio, sr, turns, voice):
    """Average, normalised embedding of up to MAX_WINDOWS 4-second stretches of one voice (None if it never speaks that long)."""
    starts = []
    for a, b, v in turns:
        if v == voice:
            t = a
            while t + WINDOW <= b:
                starts.append(t)
                t += WINDOW
    if len(starts) < 3:
        return None
    if len(starts) > MAX_WINDOWS:
        starts = [starts[int(i)] for i in np.linspace(0, len(starts) - 1, MAX_WINDOWS)]
    n = int(WINDOW * sr)
    vecs = []
    for i in range(0, len(starts), 20):
        batch = np.stack([audio[int(t * sr):int(t * sr) + n] for t in starts[i:i + 20]])
        out = emb(torch.from_numpy(batch).float().unsqueeze(1))
        vecs.append(np.asarray(out, dtype="float64"))
    v = np.concatenate(vecs)
    v = v / np.linalg.norm(v, axis=1, keepdims=True)
    m = v.mean(axis=0)
    return m / np.linalg.norm(m)


def all_voices(d03, stage3, emb, torch, session, turns, keep=False):
    """{voice: embedding} for one recording (computed from its audio, then the audio is dropped)."""
    from scipy.io import wavfile
    wav, made = d03.audio_for(stage3, session)
    sr, data = wavfile.read(str(wav))
    audio = data.astype("float32") / 32768.0
    out = {}
    for voice in sorted({v for _, _, v in turns}):
        e = voice_embedding(emb, torch, audio, sr, turns, voice)
        if e is not None:
            out[voice] = e
    if made and not keep:
        wav.unlink(missing_ok=True)
    return out


def cache_path(person, slug):
    return STORE / person / "cache" / f"{slug}.json"


def get_cached(d03, stage3, emb, torch, session, slug, person):
    p = cache_path(person, slug)
    if p.exists():
        return {k: np.array(v) for k, v in json.loads(p.read_text()).items()}
    turns = [tuple(t) for t in json.loads((ROOT / "raw" / "diarize" / f"{slug}.json").read_text())["turns"]]
    vecs = all_voices(d03, stage3, emb, torch, session, turns)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({k: v.tolist() for k, v in vecs.items()}))
    return vecs


def centroid(vectors):
    m = np.mean(vectors, axis=0)
    return m / np.linalg.norm(m)


def profile_path(person):
    return STORE / person / "profile.json"


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, person = sys.argv[1], sys.argv[2]
    rest = sys.argv[3:]
    if cmd == "forget":
        import shutil
        shutil.rmtree(STORE / person, ignore_errors=True)
        print(f"deleted the voiceprint and cache for {person}")
        return
    d03 = load_03d()
    sessions = {f"{s.get('type', 'salon')}-{int(s['number']):03d}": s for s in json.loads((ROOT / "data" / "sessions.json").read_text())}
    stage3 = d03._load("03-whisper-transcribe.py")
    emb, torch = get_embedder()

    if cmd == "enroll":
        display, slugs = rest[0], rest[1:]
        corpus = {f"{e.get('type', 'salon')}-{int(e['number']):03d}": e for e in json.loads((ROOT / "corpus" / "corpus.json").read_text())}
        prof = {"person": display, "recordings": {}}
        for slug in slugs:
            turns = [tuple(t) for t in json.loads((ROOT / "raw" / "diarize" / f"{slug}.json").read_text())["turns"]]
            truth = [(s["start"], s["end"]) for s in corpus[slug]["segments"] if s.get("speaker") == display and s.get("end")]
            overlap, total = {}, {}
            for a, b, v in turns:
                total[v] = total.get(v, 0) + (b - a)
                overlap[v] = overlap.get(v, 0) + sum(max(0, min(b, y) - max(a, x)) for x, y in truth)
            best = max(overlap, key=lambda v: overlap[v]) if overlap else None
            purity = overlap[best] / total[best] if best else 0
            vecs = get_cached(d03, stage3, emb, torch, sessions[slug], slug, person)
            ok = best in vecs and purity >= 0.9
            print(f"{slug}: voice {best} is {display} by Zoom's labels ({purity:.0%} of its speech){'' if ok else ' -- not used'}", flush=True)
            if ok:
                prof["recordings"][slug] = best
        STORE.joinpath(person).mkdir(parents=True, exist_ok=True)
        profile_path(person).write_text(json.dumps(prof, indent=1))
        print(f"enrolled {display} from {len(prof['recordings'])} recordings")

    elif cmd == "validate":
        prof = json.loads(profile_path(person).read_text())
        rec = prof["recordings"]
        pos, neg, gaps = [], [], []
        for slug, voice in rec.items():
            vecs = get_cached(d03, stage3, emb, torch, sessions[slug], slug, person)
            others = [get_cached(d03, stage3, emb, torch, sessions[s], s, person)[v] for s, v in rec.items() if s != slug]
            c = centroid(others)
            sims = {v: float(vecs[v] @ c) for v in vecs}
            pos.append(sims[voice])
            neg += [s for v, s in sims.items() if v != voice]
            second = max([s for v, s in sims.items() if v != voice], default=0)
            gaps.append(sims[voice] - second)
            print(f"{slug}: {prof['person']}'s voice {sims[voice]:.2f}; best other voice {second:.2f}", flush=True)
        lo, hi = min(pos), max(neg) if neg else 0
        print(f"the person's voice: {lo:.2f}-{max(pos):.2f} (mean {np.mean(pos):.2f}); other voices: up to {hi:.2f} (mean {np.mean(neg):.2f}); smallest gap to the runner-up {min(gaps):.2f}")
        if lo - hi > 0.08:
            prof["threshold"] = round((lo + hi) / 2, 3)
            prof["gap"] = round(min(0.10, max(0.05, min(gaps) / 2)), 3)
            profile_path(person).write_text(json.dumps(prof, indent=1))
            print(f"clean separation: THRESHOLD {prof['threshold']}, GAP {prof['gap']} saved")
        else:
            prof.pop("threshold", None)
            profile_path(person).write_text(json.dumps(prof, indent=1))
            print("the groups overlap or are too close: no threshold set, so match will suggest nothing")

    elif cmd == "match":
        prof = json.loads(profile_path(person).read_text())
        if "threshold" not in prof:
            sys.exit("no validated threshold: run validate first (and it must show a clean separation)")
        rec = prof["recordings"]
        cvecs = []
        for s, v in rec.items():
            cvecs.append(get_cached(d03, stage3, emb, torch, sessions[s], s, person)[v])
        c = centroid(cvecs)
        targets = rest if rest and rest != ["--all"] else sorted(p.stem for p in (ROOT / "raw" / "diarize").glob("*.json"))
        for slug in targets:
            path = ROOT / "raw" / "diarize" / f"{slug}.json"
            if not path.exists():
                continue
            d = json.loads(path.read_text())
            vecs = get_cached(d03, stage3, emb, torch, sessions[slug], slug, person)
            sims = sorted(((float(v @ c), k) for k, v in vecs.items()), reverse=True)
            for info in d["voices"].values():
                if str(info.get("why", "")).startswith("voice match"):
                    info.update({"candidate": None, "tier": "none", "why": "no Nametag readings for this recording"})
            hit = None
            if sims and sims[0][0] >= prof["threshold"] and (len(sims) == 1 or sims[0][0] - sims[1][0] >= prof["gap"]):
                hit = sims[0][1]
                info = d["voices"][hit]
                if info.get("tier", "none") in ("none", "single") and not info.get("name"):
                    info.update({"candidate": prof["person"], "tier": "single",
                                 "why": f"voice match: sounds like {prof['person']} (similarity {sims[0][0]:.2f}; the next voice {sims[1][0] if len(sims) > 1 else 0:.2f})"})
            path.write_text(json.dumps(d, indent=1, ensure_ascii=False))
            print(f"{slug}: {'suggested ' + hit + f' ({sims[0][0]:.2f})' if hit else 'no confident match'}"
                  f"{'' if not sims else f' [best {sims[0][0]:.2f}' + (f', next {sims[1][0]:.2f}' if len(sims) > 1 else '') + ']'}", flush=True)


if __name__ == "__main__":
    main()
