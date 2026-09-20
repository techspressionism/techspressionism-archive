#!/usr/bin/env python3
"""Suggest a voice's name from the person introducing themselves ("Hi, I'm Michael Woodruff", "my name is ...").

    python3 scripts/intro-hints.py [slug ...]        # default: every recording with a raw/diarize/<slug>.json
    python3 scripts/intro-hints.py --undo

When someone says who they are, the voice speaking at that moment is that person: the strongest evidence a transcript offers. For each
separated voice this finds its self-introductions in the finished transcript (a sentence such as "Hi, I'm X", "My name is X", "This is X
speaking"), keeps only names that are real: a full name in the people directory or in that recording's own listing, or a first name that
matches exactly one person in the listing, and sets the name as a CANDIDATE on the voice (tier "single", the reason quotes the words and
the time). NameReview shows it pre-selected; nothing is confirmed until a person confirms it, and Stage 5 ignores candidates.
A voice that introduces itself as two different people gets no hint. A voice that already has a name from Nametag/speaker index is left alone.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from lib_sentences import split_sentences  # noqa: E402
from lib_speakers import canonical_name  # noqa: E402

NAME = r"([A-Z][A-Za-zÀ-ɏ'’.-]+(?:\s+(?:[A-Z][A-Za-zÀ-ɏ'’.-]+|de|van|von|da|del|la|le|di|bin|al)){0,3})"
PATTERNS = [       # the lead-in words ignore capitals; the NAME itself must be capitalised like a name
    re.compile(r"\b(?i:hi|hello|hey|good (?:morning|afternoon|evening)|thanks?[^.]{0,30}|thank you[^.]{0,30})[\s,!.]+(?i:everyone|everybody|all|folks)?[\s,!.]*(?i:i'?m|i am|my name is|this is)\s+" + NAME),
    re.compile(r"\b(?i:my name is|my name's|my name\u2019s)\s+" + NAME),
    re.compile(r"(?:^|[.!?]\s+)(?:I'?m|I am|I\u2019m)\s+" + NAME + r"(?:\s*[,.]|\s+and\b|\s+from\b|\s+I\b|\s+(?:based|joining|zooming|calling)\b)"),
]
NOT_NAMES = {"good", "here", "just", "back", "sorry", "going", "gonna", "not", "so", "very", "really", "still", "actually", "also", "excited",
             "happy", "glad", "trying", "looking", "thinking", "working", "sharing", "showing", "iranian", "american", "canadian"}


def lexicon(sl, session):
    people = json.loads((ROOT / "data" / "people.json").read_text())["people"]
    full, listed = {}, {}
    for p in people:
        for n in [p["name"]] + p.get("aliases", []):
            n2 = re.sub(r"\s*\([^)]*\)", "", n).split(" aka ")[0].strip()
            if len(n2.split()) >= 2 and "/" not in n2:
                full[re.sub(r"[^a-z ]", "", n2.lower())] = p["name"]
    for n in [x["name"] for x in session.get("speakers", [])] + [session.get(k) for k in ("moderator", "interviewer", "interviewee")]:
        for part in re.split(r"\s+(?:and|&)\s+", re.sub(r"\s*//.*$", "", n or "")):
            part = canonical_name(part.strip())
            if part:
                listed[part] = re.sub(r"[^a-z ]", "", part.lower())
    return full, listed


def resolve(raw, full, listed):
    """A person's name for the words said, or None."""
    words = raw.strip(" .,'")
    key = re.sub(r"[^a-z ]", "", words.lower()).strip()
    if not key or key.split()[0] in NOT_NAMES:
        return None
    for name, k in listed.items():
        if k == key:
            return name
    if key in full:
        return canonical_name(full[key])
    first = [n for n, k in listed.items() if k.split()[0] == key.split()[0] and (len(key.split()) == 1 or k.split()[-1] == key.split()[-1])]
    return first[0] if len(first) == 1 else None


def main():
    undo = "--undo" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sessions = {f"{s.get('type', 'salon')}-{int(s['number']):03d}": s for s in json.loads((ROOT / "data" / "sessions.json").read_text())}
    corpus = {f"{e.get('type', 'salon')}-{int(e['number']):03d}": e for e in json.loads((ROOT / "corpus" / "corpus.json").read_text())}
    total_named = total_rec = 0
    for path in sorted((ROOT / "raw" / "diarize").glob("*.json")):
        slug = path.stem
        if args and slug not in args:
            continue
        d = json.loads(path.read_text())
        for info in d["voices"].values():
            if str(info.get("why", "")).startswith("introduces themselves"):
                info.update({"candidate": None, "tier": "none", "why": "no Nametag readings for this recording"})
        found = {}
        if not undo and slug in corpus:
            full, listed = lexicon(slug, sessions[slug])
            turns = d["turns"]
            for seg in corpus[slug]["segments"]:
                starts, times = seg.get("para_starts") or [seg.get("start")], seg.get("sentence_times") or []
                for k, para in enumerate(seg["text"].split("\n\n")):
                    sents = split_sentences(para)
                    st = times[k] if k < len(times) and len(times[k]) == len(sents) else None
                    for i, (_a, _b, txt) in enumerate(sents):
                        t = st[i] if st else (starts[k] if k < len(starts) else seg.get("start"))
                        if t is None:
                            continue
                        for rx in PATTERNS:
                            m = rx.search(txt)
                            name = resolve(m.group(1), full, listed) if m else None
                            if name:
                                voice = next((v for a, b, v in turns if a - 1 <= t + 1.5 <= b + 1), None)
                                if voice:
                                    found.setdefault(voice, []).append((name, int(t), txt[:90]))
                                break
            for voice, hits in found.items():
                names = {n for n, _, _ in hits}
                info = d["voices"][voice]
                if len(names) == 1 and info.get("tier", "none") in ("none", "single") and not info.get("name"):
                    n, t, txt = hits[0]
                    info.update({"candidate": n, "tier": "single", "why": f"introduces themselves: \"{txt}\" ({t // 60}:{t % 60:02d})" + (f" and {len(hits) - 1} more time(s)" if len(hits) > 1 else "")})
                    total_named += 1
        total_rec += 1
        path.write_text(json.dumps(d, indent=1, ensure_ascii=False))
    print(f"{'hints removed' if undo else str(total_named) + ' voices named by their own introductions'} across {total_rec} recordings")


if __name__ == "__main__":
    main()
