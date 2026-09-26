#!/usr/bin/env python3
"""Write the archive's open items into the macOS Notes note "Techspressionism Archive To Do List" (iCloud, so it shows on every device).

Source: private/archive-checklist.txt (the master checklist). The note has the two archive links under the title, then for each phase its
open items followed by the recommendations. Done items are left out. Re-running replaces the note's text; it creates the note if it is missing.

    python3 scripts/update-notes-checklist.py           # first read the note and apply Colin's edits to the master list, then rewrite the note
    python3 scripts/update-notes-checklist.py --pull    # only read the note and apply his edits (no rewrite)

The same is done for a second note, "Archive Review" (master: private/archive-review.txt, snapshot private/notes-synced-review.json): its items
come from the UI/UX and transcript review, in sections, open items only.

Two-way, so nothing he does in the note is lost: an item he deleted from the note is marked done ([x]) in the master list; a line he added
that is not in the master list is printed (Claude then files it under the right phase). Only run this on the Mac where Notes is signed in.
"""
import html
import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "private" / "archive-checklist.txt"
SNAPSHOT = ROOT / "private" / "notes-synced.json"      # the item texts written to the note last time: only these can count as "removed by Colin"
TITLE = "Archive: To Do List"
OLD_TITLES = {"Archive: To Do List": "Techspressionism Archive To Do List", "Archive: Review": "Archive Review", "Archive: Decisions": "Archive Decisions"}     # notes renamed 26 Sep 2026 ("Archive: <purpose>"): found under the old name once, then renamed
PHASE_LINES = [      # one sentence per phase, at the top of every note (Colin, 26 Sep 2026)
    "Phase 1 (archive and SEO): get the archive itself right before anything moves: search-engine setup, transcript and artist-page quality, and the search and page design.",
    "Phase 2 (redirects): put the archive live at techspressionism.com/archive and redirect the old WordPress video pages to their new archive pages.",
    "Phase 3 (stabilise and enrich): after launch, keep the archive healthy and grow it: permanent DOI, YouTube captions, the TSedit review tool, speaker naming and steady fixes.",
    "Phase 4 (site-wide SEO): use the launch data to improve search visibility for all of techspressionism.com: indexing, links, speed and content.",
]
STAGING = "https://techspressionism.github.io/techspressionism-archive/"
LIVE = "https://techspressionism.com/archive/"


def read_note(title):
    old = OLD_TITLES.get(title, title)
    r = subprocess.run(["osascript", "-e", READ % (title, title, old, old)], capture_output=True, text=True, timeout=350)
    return r.stdout


def write_note(body_html, title):
    old = OLD_TITLES.get(title, title)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(body_html)
        path = f.name
    r = subprocess.run(["osascript", "-e", SCRIPT % (path, title, title, old, old)], capture_output=True, text=True, timeout=650)
    Path(path).unlink(missing_ok=True)
    print(f"{title}:", (r.stdout or r.stderr).strip())
    return r.returncode == 0


def parse():
    phases, cur, sub = [], None, ""
    for raw in SRC.read_text().splitlines():
        line = raw.rstrip()
        m = re.match(r"PHASE (\d) - (.*)", line)
        if m:
            cur = {"n": m.group(1), "title": m.group(2), "open": [], "recs": []}
            phases.append(cur)
            sub = ""
            continue
        if not cur:
            continue
        m = re.match(r"^([A-E])\. (.*)", line)
        if m:
            sub = f"{m.group(1)}. {m.group(2)}"
            continue
        m = re.match(r"^\[( |\?)\] (.*)", line)
        if m:
            text = m.group(2)
            item = ("Decide: " if m.group(1) == "?" and not text.lower().startswith(("decide", "zoom")) else "") + text
            if "(R)" in text:
                cur["recs"].append(item.replace("(R) ", "").replace("Decide: ", ""))
            else:
                cur["open"].append((sub, item))
    return phases


def nice(t):
    """PHASE TITLES are upper case in the text file; show them as a sentence."""
    t = t.capitalize() if t.isupper() else t
    return re.sub(r"\bseo\b", "SEO", t)


def build_html(phases):
    e = html.escape
    out = [f"<h1>{e(TITLE)}</h1>",
           f'<p>Staging: <a href="{STAGING}">{STAGING}</a><br>Live: <a href="{LIVE}">{LIVE}</a></p>',
           "".join(f"<p>{e(l)}</p>" for l in PHASE_LINES)]
    for ph in phases:
        out.append(f"<h2>Phase {ph['n']} - {e(nice(ph['title']))}</h2>")
        out.append("<h3>Open items</h3>")
        if ph["open"]:
            last, items = None, []
            for sub, item in ph["open"]:
                if sub != last and items:
                    out.append("<ul>" + "".join(items) + "</ul>")
                    items = []
                if sub != last and sub:
                    out.append(f"<p><b>{e(sub)}</b></p>")
                last = sub
                items.append(f"<li>{e(item)}</li>")
            out.append("<ul>" + "".join(items) + "</ul>")
        else:
            out.append("<p>Nothing open.</p>")
        if ph["recs"]:
            out.append("<h3>Recommendations</h3><ul>" + "".join(f"<li>{e(r)}</li>" for r in ph["recs"]) + "</ul>")
    return "\n".join(out)


def item_texts(phases):
    """The exact text each open item and recommendation has in the note -> the master-list line it came from."""
    out = {}
    for ph in phases:
        for _, item in ph["open"]:
            out[item.strip()] = item
        for r in ph["recs"]:
            out[r.strip()] = r
    return out


READ = '''
with timeout of 300 seconds
tell application "Notes"
    tell account "iCloud"
        if (count of (notes whose name is "%s")) > 0 then return plaintext of (first note whose name is "%s")
        if (count of (notes whose name is "%s")) > 0 then return plaintext of (first note whose name is "%s")
        return ""
    end tell
end tell
end timeout
'''


def pull():
    """Compare the note with the master list. Returns (marked done, new lines)."""
    note = read_note(TITLE)
    if not note.strip():
        print("no note yet (or it could not be read): nothing to pull")
        return [], []
    lines = {re.sub(r"^[\u2022\-\*]\s*", "", l).strip() for l in note.splitlines() if l.strip()}
    phases = parse()
    known = item_texts(phases)
    written = set(json.loads(SNAPSHOT.read_text())) if SNAPSHOT.exists() else set()
    gone = [t for t in known if t in written and t not in lines]      # items added to the master list since the last write are not in the note yet: not "removed"
    headings = {TITLE, "Open items", "Recommendations", "Nothing open.", f"Staging: {STAGING} Live: {LIVE}", f"Staging: {STAGING}", f"Live: {LIVE}"}
    heads = {f"Phase {ph['n']} - {nice(ph['title'])}" for ph in phases} | {sub for ph in phases for sub, _ in ph["open"] if sub}
    new = [l for l in lines if l not in known and l not in written and l not in headings and l not in heads and not l.startswith(("Staging:", "Live:", "Phase "))
           and l not in OLD_TITLES.values() and l not in PHASE_LINES]
    if gone:
        text = SRC.read_text()
        for t in gone:
            src = known[t].replace("Decide: ", "")
            m = re.search(r"^\[[ ?]\] (?:\(R\) )?" + re.escape(src[:60]), text, re.M)
            if m:
                text = text[:m.start()] + "[x]" + text[m.start() + 3:]
        SRC.write_text(text)
        Path("/Users/colin/Desktop/Techspressionism-Archive-Checklist.txt").write_text(text)
    print(f"from the note: {len(gone)} item(s) removed by Colin, marked done in the master list; {len(new)} new line(s)")
    for t in gone:
        print("  done:", t[:110])
    for t in new:
        print("  NEW:", t[:160])
    return gone, new


SCRIPT = '''
with timeout of 600 seconds
set theBody to read (POSIX file "%s") as «class utf8»
tell application "Notes"
    tell account "iCloud"
        if (count of (notes whose name is "%s")) > 0 then
            set body of (first note whose name is "%s") to theBody
            return "updated"
        else if (count of (notes whose name is "%s")) > 0 then
            set body of (first note whose name is "%s") to theBody
            return "updated (renamed)"
        else
            make new note at default folder with properties {body:theBody}
            return "created"
        end if
    end tell
end tell
end timeout
'''


REVIEW_SRC = ROOT / "private" / "archive-review.txt"
REVIEW_SNAPSHOT = ROOT / "private" / "notes-synced-review.json"
REVIEW_TITLE = "Archive: Review"


def parse_review():
    """(intro lines, [(section title, [open item texts])]) from private/archive-review.txt; done items ([x]) are left out."""
    intro, secs, cur = [], [], None
    for raw in REVIEW_SRC.read_text().splitlines():
        line = raw.rstrip()
        m = re.match(r"SECTION (.*)", line)
        if m:
            cur = (m.group(1).strip(), [])
            secs.append(cur)
            continue
        if line.startswith("LOG"):
            cur = None
            continue
        m = re.match(r"^\[( |\?)\] (.*)", line)
        if m and cur is not None:
            cur[1].append(("Decide: " if m.group(1) == "?" and not m.group(2).lower().startswith("decide") else "") + m.group(2))
        elif cur is None and not secs and line.strip() and not line.startswith(("ARCHIVE REVIEW", "Key:")):
            intro.append(line)
    return intro, secs


def review_html(intro, secs):
    e = html.escape
    out = [f"<h1>{e(REVIEW_TITLE)}</h1>", "".join(f"<p>{e(l)}</p>" for l in PHASE_LINES)] + [f"<p>{e(l)}</p>" for l in intro]
    for title, items in secs:
        out.append(f"<h2>{e(title)}</h2>")
        out.append("<ul>" + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul>" if items else "<p>Nothing open.</p>")
    return "\n".join(out)


def pull_review():
    """Apply Colin's edits in the Archive Review note to private/archive-review.txt. Returns (marked done, new lines)."""
    if not REVIEW_SRC.exists():
        return [], []
    note = read_note(REVIEW_TITLE)
    if not note.strip():
        print("Archive Review: no note yet (or it could not be read): nothing to pull")
        return [], []
    lines = {re.sub(r"^[\u2022\-\*]\s*", "", l).strip() for l in note.splitlines() if l.strip()}
    intro, secs = parse_review()
    known = {i.strip(): i for _, items in secs for i in items}
    written = set(json.loads(REVIEW_SNAPSHOT.read_text())) if REVIEW_SNAPSHOT.exists() else set()
    gone = [t for t in known if t in written and t not in lines]
    heads = {REVIEW_TITLE, "Nothing open."} | {t for t, _ in secs} | {l.strip() for l in intro}
    new = [l for l in lines if l not in known and l not in written and l not in heads and l not in OLD_TITLES.values() and l not in PHASE_LINES]
    if gone:
        text = REVIEW_SRC.read_text()
        for t in gone:
            src = known[t].replace("Decide: ", "")
            m = re.search(r"^\[[ ?]\] " + re.escape(src[:60]), text, re.M)
            if m:
                text = text[:m.start()] + "[x]" + text[m.start() + 3:]
        REVIEW_SRC.write_text(text)
    print(f"Archive Review note: {len(gone)} item(s) removed by Colin, marked done; {len(new)} new line(s)")
    for t in gone:
        print("  done:", t[:110])
    for t in new:
        print("  NEW:", t[:160])
    return gone, new


def write_review():
    intro, secs = parse_review()
    if write_note(review_html(intro, secs), REVIEW_TITLE):
        REVIEW_SNAPSHOT.write_text(json.dumps(sorted(i.strip() for _, items in secs for i in items)))


DEC_SRC = ROOT / "private" / "archive-decisions.txt"
DEC_SNAPSHOT = ROOT / "private" / "notes-synced-decisions.json"
DEC_TITLE = "Archive: Decisions"

# ---- answer notes: items with an ANSWER: line Colin types into the note (master files private/archive-*.txt) --------------------------------------
ANSWER_NOTES = [
    {"title": DEC_TITLE, "src": ROOT / "private" / "archive-decisions.txt", "snap": ROOT / "private" / "notes-synced-decisions.json", "prefix": "D", "head": "ARCHIVE DECISIONS"},
    {"title": "Archive: Techspressionism Mishearings", "src": ROOT / "private" / "archive-mishearings.txt", "snap": ROOT / "private" / "notes-synced-mishearings.json", "prefix": "M", "head": "ARCHIVE MISHEARINGS"},
    {"title": "Archive: Interview 1 Turns", "src": ROOT / "private" / "archive-interview1.txt", "snap": ROOT / "private" / "notes-synced-interview1.json", "prefix": "I", "head": "ARCHIVE INTERVIEW 1"},
    {"title": "Archive: Broken Artist Links", "src": ROOT / "private" / "archive-brokenlinks.txt", "snap": ROOT / "private" / "notes-synced-brokenlinks.json", "prefix": "L", "head": "ARCHIVE BROKEN LINKS"},
    {"title": "Archive: TSedit Plan", "src": ROOT / "private" / "archive-tsedit-plan.txt", "snap": ROOT / "private" / "notes-synced-tsedit.json", "prefix": "P", "head": "ARCHIVE TSEDIT PLAN"},
]


def parse_answer_note(cfg):
    """(intro lines, [{id, status, text, answer}]) from the note's master file."""
    intro, items, cur = [], [], None
    for raw in cfg["src"].read_text().splitlines():
        line = raw.rstrip()
        m = re.match(r"^\[( |a|x)\] (" + cfg["prefix"] + r"\d+)\. (.*)", line)
        if m:
            cur = {"status": m.group(1), "id": m.group(2), "text": m.group(3), "answer": ""}
            items.append(cur)
            continue
        m = re.match(r"^\s+ANSWER:\s*(.*)", line)
        if m and cur is not None:
            cur["answer"] = m.group(1).strip()
            continue
        if line.startswith("LOG"):
            cur = None
        elif cur is None and not items and line.strip() and not line.startswith(cfg["head"]):
            intro.append(line)
    return intro, items


def answer_note_html(cfg, intro, items):
    e = html.escape
    out = [f"<h1>{e(cfg['title'])}</h1>", "".join(f"<p>{e(l)}</p>" for l in PHASE_LINES)] + [f"<p>{e(l)}</p>" for l in intro]
    open_items = [i for i in items if i["status"] != "x"]
    if not open_items:
        out.append("<p>Nothing waiting for you.</p>")
    for i in open_items:
        out.append(f"<p><b>{e(i['id'])}. {e(i['text'])}</b></p>")
        out.append(f"<p>ANSWER: {e(i['answer'])}</p>")
    return "\n".join(out)


def pull_answer_note(cfg):
    """Read Colin's answers from the note into the master file (status [a]); an item he deleted from the note is marked [x]. Returns new answers."""
    if not cfg["src"].exists():
        return []
    note = read_note(cfg["title"])
    if not note.strip():
        return []
    answers, present, cur = {}, set(), None
    for l in note.splitlines():
        m = re.match(r"^\s*(" + cfg["prefix"] + r"\d+)\.", l)
        if m:
            cur = m.group(1)
            present.add(cur)
            continue
        m = re.match(r"^\s*ANSWER:\s*(.*)", l)
        if m and cur:
            answers[cur] = m.group(1).strip()
            cur = None
    intro, items = parse_answer_note(cfg)
    written = set(json.loads(cfg["snap"].read_text())) if cfg["snap"].exists() else set()
    text = cfg["src"].read_text()
    new_answers = []
    for i in items:
        if i["status"] == "x":
            continue
        if i["id"] in written and i["id"] not in present:
            text = re.sub(r"^\[[ a]\] " + i["id"] + r"\.", "[x] " + i["id"] + ".", text, flags=re.M)
            continue
        a = answers.get(i["id"], "")
        if a and a != i["answer"]:
            text = re.sub(r"^(\[)[ a](\] " + i["id"] + r"\..*\n\s+ANSWER:).*$", lambda m: m.group(1) + "a" + m.group(2) + " " + a, text, flags=re.M, count=1)
            new_answers.append((i["id"], i["text"], a))
    cfg["src"].write_text(text)
    print(f"{cfg['title']}: {len(new_answers)} new answer(s)")
    for i, q, a in new_answers:
        print(f"  ANSWER {i}: {a}\n     (question: {q[:90]})")
    return new_answers


def write_answer_note(cfg):
    intro, items = parse_answer_note(cfg)
    if write_note(answer_note_html(cfg, intro, items), cfg["title"]):
        cfg["snap"].write_text(json.dumps([i["id"] for i in items if i["status"] != "x"]))


TOC_TITLE = "Archive: TOC"
CHANGELOG_TITLE = "Archive: Changelog"
NOTE_GUIDE = [       # (title, what it is for)
    (TOC_TITLE, "This list: every archive note and what it is for."),
    ("Archive: To Do List", "The master checklist for all four phases: what is open, in order, with my recommendations under each phase. Remove an item when it is done and it is marked done everywhere."),
    ("Archive: Review", "The UI/UX and transcript review: design problems, optimisation ideas, transcript fixes and search items, most important first."),
    ("Archive: Decisions", "Questions only you can answer. Type your answer after ANSWER: under each one; it is read at the next sync and the item is removed once acted on."),
    ("Archive: Techspressionism Mishearings", "Every place the transcript may have misheard Techspressionism / Techspressionist, with the sentence, recording and time. Choose A, B, C or D for each."),
    ("Archive: Interview 1 Turns", "The turns in Interview 1 with no speaker named, with times. Type who is speaking."),
    ("Archive: Broken Artist Links", "Artist links from the artist index that no longer work. Type the new address, skip or remove."),
    ("Archive: TSedit Plan", "The plan for TSedit, the private invite-only review and edit web app. Approve or change each part."),
    (CHANGELOG_TITLE, "Every push to the live site, numbered with 001 the first and the newest on top: what changed each time, and the snapshot to go back to (say: revert live to 00N)."),
]
EXTRA_GUIDE = [
    "Sync: all these notes are kept in step with the archive by Claude. It reads your answers and removed items first, then rewrites the notes. It runs at the start of a session, after every push, and when you say 'sync notes' or 'check my note'.",
    "Files on your Desktop: Techspressionism-Archive-Checklist.txt (the checklist as plain text), Yoast-video-redirects-LIVE.csv, Yoast-video-redirects-STAGING.csv (do not use publicly), Yoast-listing-page-redirects-OPTIONAL.csv, Archive-UI-UX-and-Transcript-Review.txt.",
    "Addresses: live https://techspressionism.com/archive/ ; staging https://techspressionism.github.io/techspressionism-archive/",
]


def toc_html():
    e = html.escape
    out = ["<h1>" + e(TOC_TITLE) + "</h1>"]
    out += [f"<p><b>{e(t)}</b><br>{e(d)}</p>" for t, d in NOTE_GUIDE]
    out += [f"<p>{e(x)}</p>" for x in EXTRA_GUIDE]
    return "\n".join(out)


def write_static_notes():
    """Notes that are only generated (no answers to read back): the table of contents and the changelog."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import changelog
    phases = "".join(f"<p>{html.escape(l)}</p>" for l in PHASE_LINES)
    for title, body in ((TOC_TITLE, toc_html()), (CHANGELOG_TITLE, changelog.render_html())):
        write_note(body.replace("</h1>", "</h1>" + phases, 1), title)


def main():
    import sys
    gone, new = pull()
    rgone, rnew = pull_review()
    for cfg in ANSWER_NOTES:
        pull_answer_note(cfg)
    if "--pull" in sys.argv:
        return
    if new or rnew:
        print("New lines in a note are not in its master list yet: add them to private/archive-checklist.txt or private/archive-review.txt first (Claude does this), then run again.")
        return
    write_review()
    for cfg in ANSWER_NOTES:
        if cfg["src"].exists():
            write_answer_note(cfg)
    phases = parse()
    if write_note(build_html(phases), TITLE):
        SNAPSHOT.write_text(json.dumps(sorted(item_texts(phases))))
    write_static_notes()


if __name__ == "__main__":
    main()
