#!/usr/bin/env python3
"""Build the master files of the answer notes that need a list generated from the archive's data (Colin, 26 Sep 2026):

  private/archive-mishearings.txt  every remaining ambiguous "Techspressionism" mishearing, with its sentence, recording and time; choose A/B/C/D
  private/archive-interview1.txt   the turns of Interview 1 that have no speaker, with times
  private/archive-brokenlinks.txt  artist links that do not work in the artist index (from review/artist-links-dropped.csv)
Also writes review/techspressionism-mishearings.csv. Run again to refresh (answers already given are kept by update-notes-checklist.py, not here:
run this only when the master file does not exist yet, or pass --force).

    python3 scripts/make-answer-notes.py [--force]
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORCE = "--force" in sys.argv
corpus = json.loads((ROOT / "corpus" / "corpus.json").read_text())


def hms(t):
    t = int(float(t or 0))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def lab(e):
    return f"{e['type'].title()} {int(e['number'])}"


# ---- mishearings ------------------------------------------------------------------------------------------------------------------------------
PH = r"text expressions?|texpressions?|text preciousness|text press(?!ion)|text fresh(?:men)?|text fashion|text salon|text community|text crescent"
rx = re.compile(r"\b(" + PH + r")\b", re.I)
rows = []
for e in corpus:
    for s in e["segments"]:
        t = s.get("text") or ""
        if not rx.search(t):
            continue
        for pi, p in enumerate(t.split("\n\n")):
            for m in rx.finditer(p):
                a = max(p.rfind(". ", 0, m.start()), p.rfind("? ", 0, m.start()), p.rfind("! ", 0, m.start())) + 1
                ends = [x for x in (p.find(". ", m.end()), p.find("? ", m.end()), p.find("! ", m.end())) if x >= 0]
                b = min(ends) + 1 if ends else len(p)
                sent = p[a:b].strip().replace("\n", " ")
                st = s.get("para_starts") or [s.get("start")]
                times = (s.get("sentence_times") or [[]])
                idx = len(re.findall(r"[.?!]\s", p[:m.start()]))
                tm = times[pi][idx] if pi < len(times) and idx < len(times[pi]) else (st[pi] if pi < len(st) and st[pi] is not None else s.get("start"))
                rows.append((e, m.group(0), sent, tm))
if FORCE or not (ROOT / "private" / "archive-mishearings.txt").exists():
    out = ["ARCHIVE MISHEARINGS",
           "Each line is a place where the transcript has a phrase that may be a mishearing of Techspressionism / Techspressionist. Type ONE letter after ANSWER: "
           "A = Techspressionism, B = Techspressionist, C = Techspressionists, D = leave as it is. Claude reads your letters when it syncs, changes the transcripts, and removes the item.",
           "Status: [ ] open   [a] answered, waiting for Claude   [x] done", ""]
    for k, (e, phrase, sent, tm) in enumerate(rows, 1):
        out.append(f"[ ] M{k}. {lab(e)}, \"{e.get('session_title') or ''}\", {hms(tm)}: \"{sent}\"  [heard as: {phrase}]")
        out.append("    ANSWER:")
    out += ["", "LOG", "26 Sep 2026  Created."]
    (ROOT / "private" / "archive-mishearings.txt").write_text("\n".join(out) + "\n")
with open(ROOT / "review" / "techspressionism-mishearings.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "recording type", "number", "title", "time", "heard as", "sentence", "choice (A Techspressionism / B Techspressionist / C Techspressionists / D leave)"])
    for k, (e, phrase, sent, tm) in enumerate(rows, 1):
        w.writerow([f"M{k}", e["type"], int(e["number"]), e.get("session_title") or "", hms(tm), phrase, sent, ""])
print(len(rows), "mishearing instances")

# ---- Interview 1 turns without a speaker -----------------------------------------------------------------------------------------------------
e1 = next(e for e in corpus if e["type"] == "interview" and int(e["number"]) == 1)
unat = [s for s in e1["segments"] if not s.get("speaker") or str(s.get("speaker")).lower().startswith("unattributed")]
if FORCE or not (ROOT / "private" / "archive-interview1.txt").exists():
    names = ", ".join(sp["name"] for sp in e1.get("speakers") or []) or "the two speakers"
    out = ["ARCHIVE INTERVIEW 1",
           f"Interview 1 has {len(unat)} turns with no speaker named. Listen at the time shown and type the speaker after ANSWER: (the speakers are: {names}; or type 'unknown'). Claude reads your answers when it syncs and records them.",
           "Status: [ ] open   [a] answered, waiting for Claude   [x] done", ""]
    for k, s in enumerate(unat, 1):
        txt = (s.get("text") or "").replace("\n", " ")
        out.append(f"[ ] I{k}. {hms(s.get('start'))} to {hms(s.get('end'))}: \"{txt[:230]}{'...' if len(txt) > 230 else ''}\"")
        out.append("    ANSWER:")
    out += ["", "LOG", "26 Sep 2026  Created."]
    (ROOT / "private" / "archive-interview1.txt").write_text("\n".join(out) + "\n")
print(len(unat), "unattributed turns in Interview 1")

# ---- broken artist links ----------------------------------------------------------------------------------------------------------------------
bl = [r for r in csv.DictReader(open(ROOT / "review" / "artist-links-dropped.csv")) if r["why"].startswith("the index link is broken")]
if FORCE or not (ROOT / "private" / "archive-brokenlinks.txt").exists():
    out = ["ARCHIVE BROKEN LINKS",
           "These artist links from the artist index on techspressionism.com no longer work, so the link is not shown on the artist's archive page. Type the new working address after ANSWER: "
           "(or 'skip' to leave it off, or 'remove' if the artist no longer wants it). Claude adds it to the archive; also update it in the WordPress artist index.",
           "Status: [ ] open   [a] answered, waiting for Claude   [x] done", ""]
    for k, r in enumerate(bl, 1):
        out.append(f"[ ] L{k}. {r['artist']} - {r['link type']}: {r['why'].split(': ', 1)[-1]}")
        out.append("    ANSWER:")
    out += ["", "LOG", "26 Sep 2026  Created."]
    (ROOT / "private" / "archive-brokenlinks.txt").write_text("\n".join(out) + "\n")
print(len(bl), "broken links")
