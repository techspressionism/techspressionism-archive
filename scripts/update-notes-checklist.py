#!/usr/bin/env python3
"""Keep the archive's notes in step with the archive, as sections of ONE Apple Note: the existing note "TECHSPRESSIONISM" (iCloud, so it
shows on every device), under its "ARCHIVE" heading.

The note is shared: Colin and other chats (Claude, GPT) add their own sections to it. This script only ever writes the part between the
"ARCHIVE" heading (with its line of dashes) and the line "-- end of ARCHIVE ..." that it maintains itself; everything else in the note is
carried through untouched, and after every write it checks that the text outside that part is unchanged (and restores the note if not).
It refuses to write if the note has attachments (a rewrite would drop them). Other material goes in its own section BELOW the end line.

Inside ARCHIVE the sections are labelled "Archive: <name>": TOC, To Do List, Review, Open Questions, Broken Artist Links, One-Turn Artists
(a fixed snapshot) and Changelog. Same two-way behaviour as before (an item Colin deletes is marked done, an ANSWER: line he types is read
back), now read from and written to those sections:

    python3 scripts/update-notes-checklist.py           # first read the note and apply Colin's edits to the master lists, then rewrite the sections
    python3 scripts/update-notes-checklist.py --pull    # only read the note and apply his edits (no rewrite)

Masters: private/archive-checklist.txt (To Do List), archive-review.txt, archive-open-questions.txt, archive-brokenlinks.txt, the push log.
Only run this on the Mac where Notes is signed in.
"""
import html
import hashlib
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "private" / "archive-checklist.txt"
SNAPSHOT = ROOT / "private" / "notes-synced.json"      # the item texts written to the note last time: only these can count as "removed by Colin"
TITLE = "Archive: To Do List"
OLD_TITLES = {"Archive: To Do List": "Techspressionism Archive To Do List", "Archive: Review": "Archive Review", "Archive: Decisions": "Archive Decisions"}     # notes renamed 26 Sep 2026 ("Archive: <purpose>"): found under the old name once, then renamed
PHASE_LINES = [      # one sentence per phase; shown once under that phase's own heading in the To Do List only (Colin, 2026-09-30 -- was repeated at the top of every note before that)
    "Phase 1 (archive and SEO): get the archive itself right before anything moves: search-engine setup, transcript and artist-page quality, and the search and page design.",
    "Phase 2 (redirects): put the archive live at techspressionism.com/archive and redirect the old WordPress video pages to their new archive pages.",
    "Phase 3 (stabilise and enrich): after launch, keep the archive healthy and grow it: permanent DOI, YouTube captions, the TSedit review tool, speaker naming and steady fixes.",
    "Phase 4 (site-wide SEO): use the launch data to improve search visibility for all of techspressionism.com: indexing, links, speed and content.",
]
STAGING = "https://techspressionism.github.io/techspressionism-archive/"
LIVE = "https://techspressionism.com/archive/"


# ---- the hub note ---------------------------------------------------------------------------------------------------------------------
HUB_TITLE = "TECHSPRESSIONISM"
HUB_ID_FILE = ROOT / "private" / "notes-hub-id.txt"       # the Notes id of the one TECHSPRESSIONISM note that has the ARCHIVE heading (several notes share that title)
HUB_BACKUPS = ROOT / "private" / "notes-backup"
HUB_HASH_FILE = ROOT / "private" / "notes-hub-lasthash.txt"     # hash of the ARCHIVE content written last time: an unchanged archive is not rewritten into the note
ARCHIVE_HEAD = "ARCHIVE"
END_KEY = "—— end of ARCHIVE"
END_MARK = END_KEY + ": the sections above are rewritten by the archive sync; add your own sections below this line ——"
PENDING = {}          # section title -> (html, on_success): collected during a run, written to the note in one go by flush_hub()

AS_COMMON = '''
on writeFile(path, txt)
    set fh to open for access (POSIX file path) with write permission
    set eof of fh to 0
    write txt to fh as «class utf8»
    close access fh
end writeFile
'''
AS_READ = '''
on run argv
    set noteId to item 1 of argv
    with timeout of 300 seconds
        tell application "Notes"
            set n to note id noteId
            my writeFile(item 2 of argv, plaintext of n)
            my writeFile(item 3 of argv, body of n)
            return (count of attachments of n) as string
        end tell
    end timeout
end run
''' + AS_COMMON
AS_WRITE = '''
on run argv
    set noteId to item 1 of argv
    with timeout of 600 seconds
        set theBody to read (POSIX file (item 2 of argv)) as «class utf8»
        tell application "Notes"
            set body of (note id noteId) to theBody
            return "ok"
        end tell
    end timeout
end run
'''
AS_FIND = '''
with timeout of 300 seconds
tell application "Notes"
    tell account "iCloud"
        set out to ""
        repeat with n in (notes whose name is "%s")
            if (plaintext of n) contains "%s" then set out to out & (id of n) & linefeed
        end repeat
        return out
    end tell
end tell
end timeout
'''


class HubError(Exception):
    pass


def _osa(script, *args, timeout=700):
    r = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise HubError((r.stderr or r.stdout).strip()[:300])
    return r.stdout.strip()


def hub_id():
    if HUB_ID_FILE.exists() and HUB_ID_FILE.read_text().strip():
        return HUB_ID_FILE.read_text().strip()
    ids = [x.strip() for x in _osa(AS_FIND % (HUB_TITLE, ARCHIVE_HEAD)).splitlines() if x.strip()]
    if len(ids) != 1:
        raise HubError(f"expected exactly one note titled {HUB_TITLE} with an {ARCHIVE_HEAD} heading, found {len(ids)}")
    HUB_ID_FILE.write_text(ids[0] + "\n")
    return ids[0]


class Hub:
    """The TECHSPRESSIONISM note as read from Notes: plain text, HTML, attachment count."""
    def __init__(self):
        self.id = hub_id()
        with tempfile.TemporaryDirectory() as d:
            pp, hp = str(Path(d) / "plain.txt"), str(Path(d) / "body.html")
            self.attachments = int(_osa(AS_READ, self.id, pp, hp) or 0)
            self.plain = Path(pp).read_text(encoding="utf-8")
            self.html = Path(hp).read_text(encoding="utf-8")

    def region_bounds(self):
        """(plain-text lines, index of the first line after the ARCHIVE divider, index of the end-marker line or None)."""
        lines = self.plain.split("\n")
        starts = [i for i in range(len(lines) - 1) if lines[i].strip() == ARCHIVE_HEAD and re.fullmatch("—{3,}", lines[i + 1].strip())]
        if len(starts) != 1:
            raise HubError(f'the note must have exactly one "{ARCHIVE_HEAD}" heading followed by a line of dashes (found {len(starts)})')
        first = starts[0] + 2
        ends = [i for i in range(first, len(lines)) if lines[i].strip().startswith(END_KEY)]
        return lines, first, (ends[0] if ends else None)

    def region_lines(self):
        lines, first, end = self.region_bounds()
        return lines[first:end] if end is not None else lines[first:]

    def outside_text(self):
        lines, first, end = self.region_bounds()
        keep = lines[:first] + (lines[end + 1:] if end is not None else [])
        return [l.strip() for l in keep if l.strip()]


_HUB = None


def get_hub(fresh=False):
    global _HUB
    if _HUB is None or fresh:
        _HUB = Hub()
    return _HUB


def read_note(title):
    """The text of one section ("Archive: <name>") of the ARCHIVE area, heading line included; empty if the section is not there yet."""
    try:
        lines = get_hub().region_lines()
    except HubError as e:
        print("could not read the note:", e)
        return ""
    heads = set(SECTION_ORDER)
    out, on = [], False
    for l in lines:
        t = l.strip()
        if t in heads:
            if on:
                break
            on = (t == title)
        if on:
            out.append(l)
    return "\n".join(out)


def write_note(body_html, title, on_success=None):
    """Queue one section's HTML; flush_hub() writes them all into the note at the end of the run."""
    PENDING[title] = (body_html, on_success)
    return True


def demote(h):
    """A generated note (h1 title, h2 headings, h3 sub-headings) becomes a section: h2 title, h3 headings, bold sub-heading lines."""
    h = re.sub(r"<h3>(.*?)</h3>", r"<p><b>\1</b></p>", h)
    h = re.sub(r"<h2>(.*?)</h2>", r"<h3>\1</h3>", h)
    return re.sub(r"<h1>(.*?)</h1>", r"<h2>\1</h2>", h)


def _region_in_html(html):
    m = list(re.finditer(r"<div>(?:<[^>]+>)*" + ARCHIVE_HEAD + r"(?:</[^>]+>)*(?:<br>)?</div>\s*<div>(?:<[^>]+>)*—{3,}(?:</[^>]+>)*(?:<br>)?</div>", html))
    if len(m) != 1:
        raise HubError(f"could not find the {ARCHIVE_HEAD} heading and dashes in the note's HTML ({len(m)} matches)")
    start = m[0].end()
    e = re.search(r"<div>(?:<[^>]+>)*" + re.escape(END_KEY) + r"[^<]*(?:</[^>]+>)*(?:<br>)?</div>", html[start:])
    if e:
        return start, start + e.end()
    if re.sub(r"<[^>]+>|\s|&nbsp;", "", html[start:]):
        raise HubError("there is content after the ARCHIVE heading but no end-of-ARCHIVE line: refusing to overwrite it")
    return start, len(html)


def _set_body(hub_id_, html):
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        path = f.name
    try:
        _osa(AS_WRITE, hub_id_, path)
    finally:
        Path(path).unlink(missing_ok=True)


def flush_hub():
    """Write every queued section into the note between the ARCHIVE divider and the end line; leave the rest of the note as it is."""
    if not PENDING:
        return False
    missing = [t for t in SECTION_ORDER if t not in PENDING]
    if missing:
        print("not writing the note: no content for", missing)
        return False
    managed = "\n<div><br></div>\n".join(demote(PENDING[t][0]) for t in SECTION_ORDER)
    digest = hashlib.sha256(managed.encode("utf-8")).hexdigest()
    if "--dry-run" not in __import__("sys").argv and HUB_HASH_FILE.exists() and HUB_HASH_FILE.read_text().strip() == digest:
        try:
            get_hub(fresh=True).region_bounds()
            print(f"{HUB_TITLE} note: ARCHIVE area already up to date, not rewritten")
            PENDING.clear()
            return True
        except HubError:
            pass          # the note's ARCHIVE area is not as expected: fall through to the normal write, which reports the problem
    try:
        hub = get_hub(fresh=True)
        real = hub.attachments - hub.html.count("<table")          # a table counts as an attachment in Notes but survives a rewrite; images and files do not
        if real > 0:
            raise HubError(f"the note has {real} attachment(s) (image or file); a rewrite would drop them. Move them to another note or tell Claude.")
        start, end = _region_in_html(hub.html)
        new_html = hub.html[:start] + "\n" + managed + "\n<div>" + END_MARK + "</div>\n" + hub.html[end:]
        if "--dry-run" in __import__("sys").argv:
            Path("/tmp/hub_new.html").write_text(new_html, encoding="utf-8")
            print(f"dry run: would write {len(new_html)} characters into the note (saved to /tmp/hub_new.html); the note was not touched")
            PENDING.clear()
            return False
        before_outside = hub.outside_text()
        HUB_BACKUPS.mkdir(parents=True, exist_ok=True)
        (HUB_BACKUPS / time.strftime("hub-%Y%m%d-%H%M%S.html")).write_text(hub.html, encoding="utf-8")
        for old in sorted(HUB_BACKUPS.glob("hub-*.html"))[:-20]:
            old.unlink()
        _set_body(hub.id, new_html)
        after = get_hub(fresh=True)
        if after.outside_text() != before_outside:
            _set_body(hub.id, hub.html)
            raise HubError("the text outside the ARCHIVE area changed during the write: the note was restored from the copy taken just before (kept in private/notes-backup/)")
    except HubError as e:
        print("TECHSPRESSIONISM note NOT updated:", e)
        PENDING.clear()
        return False
    HUB_HASH_FILE.write_text(digest + "\n")
    for _, cb in PENDING.values():
        if cb:
            cb()
    print(f"{HUB_TITLE} note: ARCHIVE area updated ({len(SECTION_ORDER)} sections)")
    PENDING.clear()
    return True


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
           f'<p>Staging: <a href="{STAGING}">{STAGING}</a><br>Live: <a href="{LIVE}">{LIVE}</a></p>']
    for ph in phases:
        out.append(f"<h2>Phase {ph['n']} - {e(nice(ph['title']))}</h2>")
        n = int(ph["n"])
        if 1 <= n <= len(PHASE_LINES):
            out.append(f"<p><i>{e(PHASE_LINES[n - 1])}</i></p>")
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
    out = [f"<h1>{e(REVIEW_TITLE)}</h1>"] + [f"<p>{e(l)}</p>" for l in intro]
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
    snap = json.dumps(sorted(i.strip() for _, items in secs for i in items))
    write_note(review_html(intro, secs), REVIEW_TITLE, lambda: REVIEW_SNAPSHOT.write_text(snap))


# ---- answer notes: items with an ANSWER: line Colin types into the note (master files private/archive-*.txt) --------------------------------------
# Consolidated 2026-09-30 (Colin: too many separate archive notes): Decisions, Techspressionism Mishearings and
# Interview 1 Turns merged into one "Archive: Open Questions" note (private/archive-open-questions.txt), item
# ids keeping their original D/I prefix -- "prefix" here is a regex fragment, not a literal string, so it can be
# an alternation. TSedit Plan retired outright (the tool is built; its one live thread, deployment, is D19 in the
# merged file); Artist Pages and One-Turn Artists were already-dead TOC entries with no source file.
ANSWER_NOTES = [
    {"title": "Archive: Open Questions", "src": ROOT / "private" / "archive-open-questions.txt", "snap": ROOT / "private" / "notes-synced-openquestions.json", "prefix": "(?:D|I)", "head": "ARCHIVE OPEN QUESTIONS"},
    {"title": "Archive: Broken Artist Links", "src": ROOT / "private" / "archive-brokenlinks.txt", "snap": ROOT / "private" / "notes-synced-brokenlinks.json", "prefix": "L", "head": "ARCHIVE BROKEN LINKS"},
]


def parse_answer_note(cfg):
    """(intro lines, [{id, status, text, detail, answer}]) from the note's master file. `detail` is any indented
    lines between the header and ANSWER: (e.g. the quoted sentence in the mishearings note) -- optional, empty
    for note types that don't have one."""
    intro, items, cur = [], [], None
    for raw in cfg["src"].read_text().splitlines():
        line = raw.rstrip()
        m = re.match(r"^\[( |a|x)\] (" + cfg["prefix"] + r"\d+)\. (.*)", line)
        if m:
            cur = {"status": m.group(1), "id": m.group(2), "text": m.group(3), "detail": [], "answer": ""}
            items.append(cur)
            continue
        m = re.match(r"^\s+ANSWER:\s*(.*)", line)
        if m and cur is not None:
            cur["answer"] = m.group(1).strip()
            continue
        if line.startswith("LOG"):
            cur = None
        elif cur is not None and line.strip():
            cur["detail"].append(line.strip())
        elif cur is None and not items and line.strip() and not line.startswith(cfg["head"]):
            intro.append(line)
    return intro, items


def answer_note_html(cfg, intro, items):
    e = html.escape
    out = [f"<h1>{e(cfg['title'])}</h1>"] + [f"<p>{e(l)}</p>" for l in intro]
    open_items = [i for i in items if i["status"] != "x"]
    if not open_items:
        out.append("<p>Nothing waiting for you.</p>")
    for i in open_items:
        out.append(f"<p><b>{e(i['id'])}. {e(i['text'])}</b></p>")
        for d in i.get("detail", []):
            out.append(f"<p>{e(d)}</p>")
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
            text = re.sub(r"^(\[)[ a](\] " + i["id"] + r"\..*(?:\n\s+[^\n]*)*?\n\s+ANSWER:).*$", lambda m: m.group(1) + "a" + m.group(2) + " " + a, text, flags=re.M, count=1)
            new_answers.append((i["id"], i["text"], a))
    cfg["src"].write_text(text)
    print(f"{cfg['title']}: {len(new_answers)} new answer(s)")
    for i, q, a in new_answers:
        print(f"  ANSWER {i}: {a}\n     (question: {q[:90]})")
    return new_answers


def write_answer_note(cfg):
    intro, items = parse_answer_note(cfg)
    snap = json.dumps([i["id"] for i in items if i["status"] != "x"])
    write_note(answer_note_html(cfg, intro, items), cfg["title"], lambda: cfg["snap"].write_text(snap))


TOC_TITLE = "Archive: TOC"
CHANGELOG_TITLE = "Archive: Changelog"
ONE_TURN_TITLE = "Archive: One-Turn Artists"
ONE_TURN_SRC = ROOT / "private" / "archive-one-turn-artists.txt"
SECTION_ORDER = [TOC_TITLE, TITLE, REVIEW_TITLE, "Archive: Open Questions", "Archive: Broken Artist Links", ONE_TURN_TITLE, CHANGELOG_TITLE]
NOTE_GUIDE = [       # (title, what it is for)
    (TOC_TITLE, "This list: every section of the ARCHIVE area of this note and what it is for."),
    ("Archive: To Do List", "The master checklist for all four phases: what is open, in order, with my recommendations under each phase. Remove an item when it is done and it is marked done everywhere."),
    ("Archive: Review", "The UI/UX and transcript review: design problems, optimisation ideas, transcript fixes and search items, most important first."),
    ("Archive: Open Questions", "Questions only you can answer, across the whole project (decisions, Interview 1's unnamed turns, and anything else). Type your answer after ANSWER: under each one; it is read at the next sync and the item is removed once acted on."),
    ("Archive: Broken Artist Links", "Artist links from the artist index that no longer work. Type the new address, skip or remove."),
    (ONE_TURN_TITLE, "A fixed snapshot (28 September 2026, not updated): artists with a page who speak exactly once in the whole archive, to check each against its one recording."),
    (CHANGELOG_TITLE, "Every push to the live site, numbered with 001 the first and the newest on top: what changed each time, and the snapshot to go back to (say: revert live to 00N)."),
]
EXTRA_GUIDE = [
    "Where this is: the sections here are the 'Archive: ...' sections inside the ARCHIVE area of the TECHSPRESSIONISM note (everything between ARCHIVE and the 'end of ARCHIVE' line). Material from other chats or by hand goes in its own section BELOW that end line: a heading, a line of dashes, then the content (plain paragraphs and bullets; no attachments or images in this note, the sync cannot rewrite a note that has them).",
    "Sync: the archive sections are kept in step with the archive by Claude. It reads your answers and removed items first, then rewrites only the ARCHIVE area. It runs at the start of a session, after every push, and when you say 'sync notes' or 'check my note'.",
    "Files on your Desktop: Techspressionism-Archive-Checklist.txt (the checklist as plain text), Yoast-video-redirects-LIVE.csv, Yoast-video-redirects-STAGING.csv (do not use publicly), Yoast-listing-page-redirects-OPTIONAL.csv, Archive-UI-UX-and-Transcript-Review.txt.",
    "Addresses: live https://techspressionism.com/archive/ ; staging https://techspressionism.github.io/techspressionism-archive/",
]


def toc_html():
    e = html.escape
    out = ["<h1>" + e(TOC_TITLE) + "</h1>"]
    out += [f"<p><b>{e(t)}</b> - {e(d)}</p>" for t, d in NOTE_GUIDE]      # one line each: a line equal to a section title would be taken for that section's start
    out += [f"<p>{e(x)}</p>" for x in EXTRA_GUIDE]
    return "\n".join(out)


def one_turn_html():
    e = html.escape
    lines = ONE_TURN_SRC.read_text(encoding="utf-8").splitlines()
    out = [f"<h1>{e(ONE_TURN_TITLE)}</h1>", "<p><i>Snapshot of 28 September 2026; not updated automatically.</i></p>"]
    items = [l for l in lines if " \u2014 http" in l]
    out += [f"<p>{e(l)}</p>" for l in lines if " \u2014 http" not in l]
    out.append("<ul>" + "".join(f"<li>{e(l)}</li>" for l in items) + "</ul>")
    return "\n".join(out)


def write_static_notes():
    """Sections that are only generated (no answers to read back): the table of contents, the one-turn snapshot and the changelog."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import changelog
    for title, body in ((TOC_TITLE, toc_html()), (ONE_TURN_TITLE, one_turn_html() if ONE_TURN_SRC.exists() else "<h1>" + html.escape(ONE_TURN_TITLE) + "</h1><p>Nothing here.</p>"), (CHANGELOG_TITLE, changelog.render_html())):
        write_note(body, title)


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
    snap = json.dumps(sorted(item_texts(phases)))
    write_note(build_html(phases), TITLE, lambda: SNAPSHOT.write_text(snap))
    write_static_notes()
    flush_hub()


if __name__ == "__main__":
    main()
