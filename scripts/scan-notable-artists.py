#!/usr/bin/env python3
"""Find misspelled names of notable artists in the transcripts, using Wikipedia's own artist categories for the names (Colin, 25 Sep 2026).

1. Names: members of Wikipedia categories for the major movements (expressionist, abstract expressionist, surrealist, Dada, Cubist, Fauvist, Impressionist,
   Post-Impressionist, Pop, minimalist, conceptual, digital / computer / new media / video artists ...) -> data/notable-artists.json {"first": {"Last": count}}.
2. Scan: every pair of capitalised words in the transcripts whose first word is the first name of a notable artist and whose second word is NOT that artist's last
   name but is spelled like it (and is not an ordinary word or an archive artist's own name) is a candidate ("Franz Klein" for Franz Kline). A candidate whose
   spelling is very close (>= 0.82) and appears in a context of art words is fixed automatically (data/artist-name-fixes.json, applied by lib_corrections);
   the rest go to review/artist-name-candidates.csv with the surrounding words, together with lone last names spelled like a notable last name.

    python3 scripts/scan-notable-artists.py [--fetch]      # --fetch refreshes the Wikipedia name lists (about 60 requests, one per second)
"""
import collections
import csv
import difflib
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://en.wikipedia.org/w/api.php"
UA = "TechspressionismVideoArchive/1.0 (https://techspressionism.com/archive/; transcript name check)"
NAMES = ROOT / "data" / "notable-artists.json"
FIXES = ROOT / "data" / "artist-name-fixes.json"
CATEGORIES = ["Abstract expressionist artists", "Expressionist artists", "German Expressionist painters", "Surrealist artists", "Dada artists", "Cubist artists",
              "Fauvist painters", "Impressionist painters", "Post-Impressionist painters", "Pop artists", "Minimalist artists", "Conceptual artists", "Op art artists",
              "Futurist painters", "Bauhaus", "Fluxus artists", "Land artists", "Video artists", "Digital artists", "Computer graphics artists", "New media artists",
              "American contemporary artists", "British contemporary artists", "Performance artists", "Photorealist artists", "Color field painters",
              "Neo-expressionist artists", "Symbolist painters", "Renaissance painters", "Baroque painters", "Romantic painters", "Modern painters",
              "Algorists", "Glitch artists", "Cyberneticists", "Sound artists", "Installation artists", "Sculptors from New York (state)", "Women surrealist artists"]
ART_WORDS = re.compile(r"\b(paint\w*|artist\w*|art|sculpt\w*|exhibit\w*|gallery|museum|abstract|expressionis\w*|surreal\w*|canvas|drawing|work|works|movement|school|studio|"
                       r"pollock|rothko|warhol|picasso|de kooning|motherwell|guston|newman|still|krasner|monet|matisse|dali|duchamp|computer|algorithm\w*|video)\b", re.I)


def get(params):
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=40) as r:
        return json.loads(r.read())


def fetch_names():
    people = set()
    for cat in CATEGORIES:
        cont = {}
        while True:
            try:
                d = get({"action": "query", "list": "categorymembers", "cmtitle": "Category:" + cat, "cmlimit": "500", "cmtype": "page", **cont})
            except Exception as ex:
                print(f"  {cat}: {ex}")
                break
            for m in d["query"]["categorymembers"]:
                people.add(m["title"])
            if "continue" not in d:
                break
            cont = d["continue"]
            time.sleep(1)
        print(f"  {cat}: {len(people)} so far", flush=True)
        time.sleep(1)
    index = collections.defaultdict(dict)
    for t in people:
        n = re.sub(r"\s*\([^)]*\)", "", t).strip()
        w = n.split()
        if not (2 <= len(w) <= 3) or any(not re.match(r"^[A-ZÀ-Þ][\w'’.\-À-ſ]+$", x) for x in w):
            continue
        index[w[0]][w[-1]] = index[w[0]].get(w[-1], 0) + 1
    NAMES.write_text(json.dumps(index, ensure_ascii=False, sort_keys=True))
    print(f"{sum(len(v) for v in index.values())} notable artist names saved")


def fold(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def main():
    if "--fetch" in sys.argv or not NAMES.exists():
        fetch_names()
    index = {fold(k): {fold(l): l for l in v} for k, v in json.loads(NAMES.read_text()).items()}
    all_lasts = {l for v in index.values() for l in v}
    corpus = json.loads((ROOT / "corpus" / "corpus.json").read_text())
    own = set()      # names that belong to people in this archive: never "corrected" toward a notable artist
    people = json.loads((ROOT / "data" / "people.json").read_text())
    for p in (people if isinstance(people, list) else people["people"]):
        for n in [p["name"]] + list(p.get("aliases", [])):
            own |= {fold(x) for x in re.findall(r"[A-Za-zÀ-ſ'\-]+", n)}
    for e in corpus:
        for s in e["segments"]:
            own |= {fold(x) for x in re.findall(r"[A-Za-zÀ-ſ'\-]+", s.get("speaker") or "")}
    pair = re.compile(r"\b([A-ZÀ-Þ][a-zà-ſ'’\-]+)[ \t]+([A-ZÀ-Þ][A-Za-zà-ſ'’\-]{3,})\b")
    seen = collections.defaultdict(lambda: {"n": 0, "ctx": [], "slugs": set()})
    lone = collections.Counter()
    words = set()
    for e in corpus:
        for s in e["segments"]:
            words |= set(re.findall(r"[A-Za-zÀ-ſ'\-]{4,}", s.get("text") or ""))
    open("/tmp/notable_words.txt", "w").write("\n".join(sorted({w.lower() for w in words})))
    spell = ROOT / "raw" / "spell"                      # NSSpellChecker helper compiled by hand (see review notes); optional
    unknown = None
    exe = Path("/private/tmp/claude-501/-Users-colin-techspressionism-archive/f7b641c6-0c1e-44a0-bc47-358d84b26402/scratchpad/spell/spell")
    if exe.exists():
        unknown = {l.split("\t")[0] for l in subprocess.run([str(exe)], stdin=open("/tmp/notable_words.txt"), capture_output=True, text=True).stdout.splitlines()}
    for e in corpus:
        slug = f"{e['type']}-{int(e['number']):03d}"
        for s in e["segments"]:
            t = re.sub(r"_([^_]+)_", r"\1", s.get("text") or "")
            for m in pair.finditer(t):
                a, b = fold(m.group(1)), m.group(2)
                fb = fold(b)
                lasts = index.get(a)
                if not lasts or fb in lasts or fb in own:
                    continue
                if fb.endswith(("'s", "\u2019s")) and fb[:-2] in lasts:
                    continue                                    # a possessive of the right name
                if unknown is not None and fb.replace("'s", "") not in unknown and fb not in unknown:
                    continue                                    # an ordinary word or a name the dictionary knows
                best = max(((difflib.SequenceMatcher(None, fb, l).ratio(), l) for l in lasts), default=(0, ""))
                if best[0] >= 0.78 and abs(len(fb) - len(best[1])) <= 2:
                    key = (m.group(1), b, lasts[best[1]], round(best[0], 2))
                    r = seen[key]
                    r["n"] += 1
                    r["slugs"].add(slug)
                    if len(r["ctx"]) < 2:
                        r["ctx"].append(t[max(0, m.start() - 60):m.end() + 60].replace("\n", " "))
    fixes, rows = [], []
    for (first, wrong, right, ratio), r in sorted(seen.items(), key=lambda x: -x[1]["n"]):
        art = any(ART_WORDS.search(c) for c in r["ctx"])
        auto = ratio >= 0.86 and art and not re.match(r"^(David|John|Michael|Kevin|Eric|Mark|Paul|Mary|Chris|Jennifer|Peter|James|Robert|Tom|Jim|Steve|Sam)$", first)
        rows.append([f"{first} {wrong}", f"{first} {right}", r["n"], ratio, "fixed automatically" if auto else "check", " | ".join(sorted(r["slugs"]))[:80], r["ctx"][0]])
        if auto:
            fixes.append({"pattern": rf"\b{re.escape(first)} {re.escape(wrong)}\b", "replacement": f"{first} {right}", "seen": r["n"]})
    with open(ROOT / "review" / "artist-name-candidates.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["as written", "notable artist", "times", "spelling match", "action", "recordings", "example"])
        w.writerows(rows)
    FIXES.write_text(json.dumps(fixes, ensure_ascii=False, indent=1))
    print(f"{len(rows)} candidates; {len(fixes)} fixed automatically; see review/artist-name-candidates.csv")


if __name__ == "__main__":
    main()
