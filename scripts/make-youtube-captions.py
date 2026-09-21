#!/usr/bin/env python3
"""Make YouTube caption files (SRT, or WebVTT) from the archive's corrected transcripts.

    python3 scripts/make-youtube-captions.py                 # all recordings -> private/youtube-captions/
    python3 scripts/make-youtube-captions.py --slug salon-081
    python3 scripts/make-youtube-captions.py --no-speakers   # leave out the speaker names
    python3 scripts/make-youtube-captions.py --vtt           # WebVTT instead of SRT

Each file is named <slug>_<YouTube id>.srt and is uploaded in YouTube Studio: the video > Subtitles > Add language (English) >
Upload file > "With timing". Uploaded captions are the ones viewers, search and AI tools see; YouTube's own automatic track stays
in the background. private/youtube-captions/index.csv lists every file with its checks.

How the cues are made: the corrected transcript has a start time for every paragraph and every sentence (Stage 5). A sentence is
shown from its start to the next sentence's start (limited to a reading pace, so a pause is not filled with text); a long
sentence is cut into cues of at most two lines of 42 characters and about six seconds, and the times inside it are shared out by
character count. Where Zoom made the transcript the sentence starts are good to about a second; Whisper ones are exact. The
speaker's name (identified speakers only) starts the first cue of each turn, as "Name: ". Nothing here changes the transcripts.
"""
import csv
import json
import math
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from lib_sentences import split_sentences          # noqa: E402  (the same sentence rule as the site)
from lib_speakers import is_not_speaker            # noqa: E402

OUT = ROOT / "private" / "youtube-captions"
LINE, LINES = 42, 2            # characters per line, lines per cue
MAX_SECONDS = 6.0              # longest a cue stays on screen while being read
CPS = 12.5                     # speaking pace used to cap a sentence's length: characters per second
MIN_SECONDS = 1.0


def clean(text):
    text = text.replace("\xa0", " ").replace("&nbsp;", " ")
    text = re.sub(r"\[__\]", "", text)
    text = re.sub(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])", r"\1", text)       # _Title_ markers
    return " ".join(text.split())


def chunks(sentence, seconds):
    """Cut one sentence into caption-sized pieces: [(text, share_start, share_end)] with shares 0..1 of its length."""
    limit = LINE * LINES - 2
    n = max(math.ceil(len(sentence) / limit), math.ceil(seconds / MAX_SECONDS), 1)
    words = sentence.split()
    if n == 1 or len(words) < 2:
        return [(sentence, 0.0, 1.0)]
    target = len(sentence) / n
    out, cur, pos = [], [], 0
    for w in words:
        cur.append(w)
        if len(" ".join(cur)) >= target and len(out) < n - 1:
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    total = sum(len(t) for t in out) or 1
    res = []
    for t in out:
        res.append((t, pos / total, (pos + len(t)) / total))
        pos += len(t)
    return res


def wrap(text):
    lines = textwrap.wrap(text, LINE, break_long_words=False)
    if len(lines) <= LINES:
        return lines
    half = textwrap.wrap(text, math.ceil(len(text) / LINES) + 3, break_long_words=False)     # a longer name label can push a third line
    return half if len(half) <= LINES + 1 else lines


def cues_for(entry, speakers=True):
    cues, prev = [], None
    segs = entry["segments"]
    for si, seg in enumerate(segs):
        seg_start = float(seg["start"]) if seg.get("start") is not None else (cues[-1][1] if cues else 0.0)
        following = next((float(g["start"]) for g in segs[si + 1:] if g.get("start") is not None), None)
        seg_end = float(seg["end"]) if seg.get("end") is not None else (following if following is not None else float(entry.get("duration_seconds") or seg_start + 30))
        paras = seg["text"].split("\n\n")
        pstarts = seg.get("para_starts") or []
        stimes = seg.get("sentence_times") or []
        speaker = None if (not seg.get("speaker") or is_not_speaker(seg["speaker"])) else seg["speaker"]
        first = True
        for k, para in enumerate(paras):
            sents = split_sentences(para)
            times = stimes[k] if k < len(stimes) and len(stimes[k]) == len(sents) else None
            p0 = float(pstarts[k]) if k < len(pstarts) and pstarts[k] is not None else seg_start
            if times is None:
                times = [p0] * len(sents)
            p_end = float(pstarts[k + 1]) if k + 1 < len(pstarts) and pstarts[k + 1] is not None else seg_end
            for j, (_a, _b, stext) in enumerate(sents):
                t0 = float(times[j])
                nxt = float(times[j + 1]) if j + 1 < len(times) else p_end
                txt = clean(stext)
                if not txt:
                    continue
                room = max(nxt - t0, 0.0)
                span = min(room, len(txt) / CPS + 0.5) if room > 0 else len(txt) / CPS + 0.5
                for text, a, b in chunks(txt, span):
                    if first and speakers and speaker and speaker != prev:
                        text = f"{speaker}: {text}"
                    first = False
                    cues.append([t0 + span * a, t0 + span * b, text])
        prev = speaker if speaker else prev
    # tidy: ordered, no overlap, each on screen long enough, none past the video
    cues.sort(key=lambda c: c[0])
    dur = float(entry.get("duration_seconds") or 0)
    for i, c in enumerate(cues):
        limit = cues[i + 1][0] - 0.02 if i + 1 < len(cues) else (dur or c[1] + MIN_SECONDS)
        c[1] = min(max(c[1], c[0] + MIN_SECONDS), max(limit, c[0] + 0.3))
        if dur:
            c[1] = min(c[1], dur)
    return [c for c in cues if c[1] > c[0] and (not dur or c[0] < dur)]


def ts(sec, vtt=False):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{'.' if vtt else ','}{ms:03d}"


def render(cues, vtt=False):
    out = ["WEBVTT", ""] if vtt else []
    for n, (a, b, text) in enumerate(cues, 1):
        if not vtt:
            out.append(str(n))
        out.append(f"{ts(a, vtt)} --> {ts(b, vtt)}")
        out += wrap(text)
        out.append("")
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    only = args[args.index("--slug") + 1] if "--slug" in args else None
    speakers, vtt = "--no-speakers" not in args, "--vtt" in args
    corpus = json.loads((ROOT / "corpus" / "corpus.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry in corpus:
        slug = f"{entry.get('type', 'salon')}-{int(entry['number']):03d}"
        if only and slug != only:
            continue
        cues = cues_for(entry, speakers)
        name = f"{slug}_{entry['video_id']}.{'vtt' if vtt else 'srt'}"
        (OUT / name).write_text(render(cues, vtt), encoding="utf-8")
        dur = float(entry.get("duration_seconds") or 0)
        longest = max((c[1] - c[0] for c in cues), default=0)
        widest = max((len(l) for c in cues for l in wrap(c[2])), default=0)
        rows.append({"file": name, "slug": slug, "video_id": entry["video_id"], "cues": len(cues), "video_seconds": int(dur),
                     "last_cue_end": int(cues[-1][1]) if cues else 0, "longest_cue_s": round(longest, 1), "widest_line": widest,
                     "source": entry.get("transcript_source", "")})
    with open(OUT / "index.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} caption files -> {OUT.relative_to(ROOT)}/   ({sum(r['cues'] for r in rows):,} cues)")


if __name__ == "__main__":
    main()
