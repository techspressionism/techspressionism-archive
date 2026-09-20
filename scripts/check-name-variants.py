#!/usr/bin/env python3
"""Find probable mis-hearings of people's names in the finished transcripts, for review. Nothing is changed.

    python3 scripts/check-name-variants.py

Speech recognition spells a name slightly wrong ("Renata Janiszewski", "Sasha Styles"). This looks in every passage of corpus/corpus.json for two
words in a row that are each equal or very close (letter similarity 0.8+) to the first and last name of someone in data/people.json, or of a
speaker in that recording, but are not spelled exactly like the name, and writes review/name-variant-candidates.csv:
    tier A  the wrong spelling occurs in a recording where that person is a speaker, or three or more times in two or more recordings
            (very probably a mis-hearing of that person)
    tier B  anything else (check by hand; many are real other people)
Each row: what was heard, the probable name, how often, in which recordings, an example with its time. To fix one, add the wrong spelling to
"known_asr_variants" of that person's entry in data/vocabulary.json (add the entry if there is none) and rebuild Stage 5.
"""
import csv
import difflib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from lib_speakers import is_not_speaker  # noqa: E402

WORD = re.compile(r"[A-Za-zÀ-ɏ'’-]+")


def norm(s):
    return re.sub(r"[^a-z]", "", s.lower().replace("’", "").replace("'", ""))


def main():
    corpus = json.loads((ROOT / "corpus" / "corpus.json").read_text())
    people = json.loads((ROOT / "data" / "people.json").read_text())["people"]
    names = {}
    for p in people:
        for n in [p["name"]] + p.get("aliases", []):
            n = re.sub(r"\s*\([^)]*\)", "", n).split(" aka ")[0].strip()
            t = n.split()
            if len(t) >= 2 and len(n) >= 8 and "/" not in n and "&" not in n:
                names.setdefault(n, p["name"])
    exact = {norm(n) for n in names}
    # corpus tokens
    vocab = defaultdict(int)
    for e in corpus:
        for s in e["segments"]:
            for w in WORD.findall(s["text"]):
                vocab[norm(w)] += 1
    vocab_words = [w for w in vocab if len(w) >= 3]
    close_cache = {}

    def close(tok):
        k = norm(tok)
        if k not in close_cache:
            close_cache[k] = set(difflib.get_close_matches(k, vocab_words, n=25, cutoff=0.8)) | ({k} if k in vocab else set())
        return close_cache[k]
    pairs = {}
    for n in names:
        t = [norm(x) for x in n.split()]
        if len(t[0]) >= 3 and len(t[-1]) >= 4:
            pairs[n] = (t[0], t[-1], close(t[0]), close(t[-1]))
    by_first = defaultdict(list)
    for n, (f, l, cf, cl) in pairs.items():
        for c in cf:
            by_first[c].append(n)
    print(f"{len(pairs)} names, {len(vocab_words)} distinct words", flush=True)
    found = defaultdict(lambda: {"count": 0, "recs": set(), "example": None})
    speakers_of = {}
    for e in corpus:
        slug = f"{e.get('type', 'salon')}-{int(e['number']):03d}"
        speakers_of[slug] = {norm(s["name"]) for s in e.get("speakers", [])} | {norm(e.get("moderator") or "")}
        for s in e["segments"]:
            words = [(m.group(), m.start()) for m in WORD.finditer(s["text"])]
            for i in range(len(words) - 1):
                a, b = norm(words[i][0]), norm(words[i + 1][0])
                for n in by_first.get(a, ()):
                    f, l, cf, cl = pairs[n]
                    if b in cl and not (a == f and b == l) and a + b not in exact:
                        if a != f and b != l and difflib.SequenceMatcher(None, a + b, f + l).ratio() < 0.85:
                            continue
                        key = (f"{words[i][0]} {words[i + 1][0]}".lower(), n)
                        rec = found[key]
                        rec["count"] += 1
                        rec["recs"].add(slug)
                        if rec["example"] is None:
                            st = int(s.get("start") or 0)
                            rec["example"] = (slug, st, s["text"][max(0, words[i][1] - 50):words[i][1] + 90].replace("\n", " "))
    rows = []
    for (heard, name), r in found.items():
        who = norm(name)
        in_rec = any(any(norm(x) == who or (norm(name.split()[-1]) in x and norm(name.split()[0]) in x) for x in speakers_of.get(sl, ())) for sl in r["recs"])
        tier = "A" if in_rec or (r["count"] >= 3 and len(r["recs"]) >= 2) else "B"
        rows.append([tier, heard, name, r["count"], len(r["recs"]), ", ".join(sorted(r["recs"])[:6]), *r["example"][:2], r["example"][2]])
    rows.sort(key=lambda x: (x[0], -x[3]))
    out = ROOT / "review" / "name-variant-candidates.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["tier", "heard as", "probably", "times", "recordings", "where", "example recording", "example time (s)", "example"])
        w.writerows(rows)
    print(f"{len(rows)} candidates ({sum(1 for r in rows if r[0] == 'A')} tier A) -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
