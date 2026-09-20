#!/usr/bin/env python3
"""Find places where "Techspressionism" / "Techspressionist" was probably mis-transcribed as "text-" something.

Speech recognition (Zoom, YouTube, Whisper) very often hears "Techspressionism" as "text pressionism",
"text-prussianism", "text russianism", "text fashionism" and dozens of other variants, and the variants differ from
recording to recording. Known variants are corrected automatically by data/vocabulary.json (Stage 4/5). THIS check
looks at the FINISHED pages (corpus/corpus.json) and reports what is left: any "text"/"tex" + a word that is not an
ordinary English continuation ("text message", "text and", "texture", ...) is listed for a person to decide.

    python3 scripts/check-techspressionism.py              # summary + review/techspressionism-suspects.csv
    python3 scripts/check-techspressionism.py --slug salon-101
    python3 scripts/check-techspressionism.py --all        # also list the weak (less likely) suspects

Run it after every new transcription (Stage 5 prints a reminder; the manual's procedures include it). When a variant is
confirmed, add it to data/vocabulary.json (Techspressionism or Techspressionist "known_asr_variants") and rebuild
Stage 5, and it is fixed in every recording, now and in future. When a phrase is genuinely ordinary English, add it to
data/techspressionism-ok.json ("ok_phrases") so it stops being listed.

Tiers:  LIKELY  the phrase looks like Techspressionism/-ist (sounds/letters overlap), or it is glued ("textpression").
        WEAK    a "text" + word that is not a common English word; may be a mishearing, may not.
Nothing here changes any text; it only reports.
"""
import csv
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus" / "corpus.json"
OK_PATH = ROOT / "data" / "techspressionism-ok.json"
OUT = ROOT / "review" / "techspressionism-suspects.csv"

TARGETS = ("techspressionism", "techspressionist", "techspressionists", "techspression")

# words that legitimately follow "text" (text message, text and, text to speech ...) or start with "text" (texture ...)
COMMON = set("""a about after again all also an and any are as at back based be because been before between but by can could
day did do does doing done down each else even every file files first for from get gets give go goes going good got had has
have he her here him his how i if image images in into is it its itself just like look looks made make many me message
messages messaging more most much my new no not now of off on one only or other our out over own per prompt prompts
question questions read really right said same say see she should show showing so some still such take tell than that
the their them then there these they thing things this those through to too translation two under underneath up us
version versions very want was way we well were what when where which while who why will with without work would yes you your
edit editor edits editing description descriptions box field fields format formats generation generator input inputs
output outputs line lines paragraph paragraphs string strings window windows""".split())
LEGIT_WORDS = re.compile(r"^(?:texts?|texted|texting|textile|textiles|texture|textures|textured|textural|texturally|textual|textually|textbook|textbooks)$", re.I)

WORD = re.compile(r"\b((?:text|tex|texts?)[-\s]?)([A-Za-z][A-Za-z'’]*)", re.I)
GLUED = re.compile(r"\b(text[a-z]{4,})\b", re.I)


def load_ok():
    if OK_PATH.exists():
        return {s.lower() for s in json.loads(OK_PATH.read_text()).get("ok_phrases", [])}
    return set()


def score(phrase):
    p = re.sub(r"[^a-z]", "", phrase.lower())
    return max(SequenceMatcher(None, p, t).ratio() for t in TARGETS)


def scan(text):
    found = []
    for m in WORD.finditer(text):
        nxt = m.group(2)
        phrase = (m.group(1) + nxt).replace("’", "'")
        if LEGIT_WORDS.match(m.group(1).strip("- ") + nxt) or LEGIT_WORDS.match(nxt):
            continue
        if nxt.lower() in COMMON:
            continue
        found.append((m.start(), m.end(), phrase))
    for m in GLUED.finditer(text):
        w = m.group(1)
        if LEGIT_WORDS.match(w) or any(k in w.lower() for k in ("textbook", "texture", "textile", "textual", "texting", "texted")):
            continue
        if not any(m.start() == s for s, _, _ in found):
            found.append((m.start(), m.end(), w))
    return found


def main():
    args = sys.argv[1:]
    only = args[args.index("--slug") + 1] if "--slug" in args else None
    show_weak = "--all" in args
    ok = load_ok()
    corpus = json.loads(CORPUS.read_text())
    rows = []
    for entry in corpus:
        slug = f"{entry.get('type', 'salon')}-{int(entry['number']):03d}"
        if only and slug != only:
            continue
        for seg in entry["segments"]:
            starts = seg.get("para_starts") or []
            for k, para in enumerate(seg["text"].split("\n\n")):
                for s, e, phrase in scan(para):
                    if phrase.lower() in ok:
                        continue
                    r = score(phrase)
                    tier = "LIKELY" if (r >= 0.62 or " " not in phrase and "-" not in phrase) else "WEAK"
                    t0 = starts[k] if k < len(starts) and starts[k] is not None else seg.get("start")
                    rows.append({"tier": tier, "slug": slug, "time": int(t0 or 0), "phrase": phrase, "score": f"{r:.2f}",
                                 "context": para[max(0, s - 60):e + 60].replace("\n", " ")})
    rows.sort(key=lambda r: (r["tier"] != "LIKELY", -float(r["score"]), r["phrase"].lower(), r["slug"], r["time"]))
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tier", "slug", "time", "phrase", "score", "context"])
        w.writeheader()
        w.writerows(rows)
    likely = [r for r in rows if r["tier"] == "LIKELY"]
    weak = [r for r in rows if r["tier"] == "WEAK"]
    print(f"Techspressionism check: {len(likely)} LIKELY and {len(weak)} weak suspects -> {OUT.relative_to(ROOT)}")
    from collections import Counter
    for tier, group in (("LIKELY", likely), ("WEAK", weak if show_weak else [])):
        if not group:
            continue
        print(f"\n{tier}:")
        for phrase, n in Counter(r["phrase"].lower() for r in group).most_common(40):
            ex = next(r for r in group if r["phrase"].lower() == phrase)
            print(f"  {n:3d}  {phrase!r:28}  e.g. {ex['slug']} {ex['time']}s: ...{ex['context'][30:110]}...")
    if likely:
        print("\nConfirm each real one by adding it to data/vocabulary.json, then rebuild Stage 5.")


if __name__ == "__main__":
    main()
