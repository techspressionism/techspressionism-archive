#!/usr/bin/env python3
"""Find capital letters in the middle of a sentence that should not be there ("...founders of Loop, and Have her talk about its history").

    python3 scripts/check-capitalization.py             # (re)build the word list, report -> review/capitalization-suspects.csv

Zoom starts every caption line with a capital, even when the sentence goes on. Step 1 writes data/lowercase-words.json: the words that are common
in lower case (at least 30 uses and 30 times more often than the capitalised form in mid-sentence) and are never a name (nobody in data/people.json,
data/artists.json or data/vocabulary.json is called that, and they are not on lib_corrections.KEEP_CAPITAL: months, days, brands, nationalities).
Step 2 lists every place where lib_corrections.midsentence_caps() would lower a capital (the same rule the build applies): a capitalised ordinary
word straight after a word that cannot end a sentence (and, of, the, is ...), with no other capitalised word close by in the sentence (that would be a
name or a title). Names, "I" and sentence starts are never touched.
"""
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import lib_corrections as lc  # noqa: E402

WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def proper_words():
    names = set(lc.KEEP_CAPITAL)
    for p in json.loads((ROOT / "data" / "people.json").read_text())["people"]:
        for n in [p["name"]] + p.get("aliases", []):
            names |= {w.lower() for w in WORD.findall(n)}
    for a in json.loads((ROOT / "data" / "artists.json").read_text()):
        for k in ("name", "location", "country", "region"):
            names |= {w.lower() for w in WORD.findall(a.get(k) or "")}
    for t in json.loads((ROOT / "data" / "vocabulary.json").read_text())["terms"]:
        names |= {w.lower() for w in WORD.findall(t["canonical"])}
    return names


def main():
    corpus = json.loads((ROOT / "corpus" / "corpus.json").read_text())
    lower, cap_mid = Counter(), Counter()
    for e in corpus:
        for s in e["segments"]:
            for par in s["text"].split("\n\n"):
                toks = par.split()
                for i, tok in enumerate(toks):
                    m = re.fullmatch(r"[\"'“‘(\[_]*([A-Za-z][A-Za-z'’-]*)[\"'”’)\].,;:!?_…-]*", tok)
                    if not m:
                        continue
                    w = m.group(1)
                    if w.islower():
                        lower[w] += 1
                    elif w[0].isupper() and w[1:].islower() and i > 0 and not re.search(r"[.?!…:”\"]$", toks[i - 1]):
                        cap_mid[w.lower()] += 1
    proper = proper_words()
    words = sorted(w for w, n in lower.items() if n >= 30 and n >= 30 * (cap_mid[w] or 1) and w not in proper and len(w) > 1)
    (ROOT / "data" / "lowercase-words.json").write_text(json.dumps(
        {"_comment": "Ordinary words that are never a name: lib_corrections.midsentence_caps may lower a capital on them in mid-sentence. Written by check-capitalization.py.",
         "words": words}, indent=0) + "\n")
    lc._ORDINARY.clear()
    rows, by_word = [], Counter()
    for e in corpus:
        slug = f"{e.get('type', 'salon')}-{int(e['number']):03d}"
        for s in e["segments"]:
            for par in s["text"].split("\n\n"):
                for idx, w in lc.midsentence_caps(par):
                    rows.append((w, slug, int(s.get("start") or 0), par[max(0, idx - 35):idx + len(w) + 30].replace("\n", " ")))
                    by_word[w.lower()] += 1
    out = ROOT / "review" / "capitalization-suspects.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["word", "recording", "time (s)", "context"])
        for r in sorted(rows, key=lambda x: (-by_word[x[0].lower()], x[0].lower(), x[1], x[2])):
            w.writerow(r)
    print(f"{len(words)} ordinary words; {len(rows)} capitals in mid-sentence to lower ({len(by_word)} different words) in {len({r[1] for r in rows})} recordings -> {out.relative_to(ROOT)}")
    for word, n in by_word.most_common(25):
        ex = next(r for r in rows if r[0].lower() == word)
        print(f"  {n:4d}  {word:10} {ex[1]}: ...{ex[3]}...")


if __name__ == "__main__":
    main()
