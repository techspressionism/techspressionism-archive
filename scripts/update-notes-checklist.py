#!/usr/bin/env python3
"""Write the archive's open items into the macOS Notes note "Techspressionism Archive To Do List" (iCloud, so it shows on every device).

Source: private/archive-checklist.txt (the master checklist). The note has the two archive links under the title, then for each phase its
open items followed by the recommendations. Done items are left out. Re-running replaces the note's text; it creates the note if it is missing.

    python3 scripts/update-notes-checklist.py           # first read the note and apply Colin's edits to the master list, then rewrite the note
    python3 scripts/update-notes-checklist.py --pull    # only read the note and apply his edits (no rewrite)

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
TITLE = "Techspressionism Archive To Do List"
STAGING = "https://techspressionism.github.io/techspressionism-archive/"
LIVE = "https://techspressionism.com/archive/"


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
        if (count of (notes whose name is "%s")) = 0 then return ""
        return plaintext of (first note whose name is "%s")
    end tell
end tell
end timeout
'''


def pull():
    """Compare the note with the master list. Returns (marked done, new lines)."""
    r = subprocess.run(["osascript", "-e", READ % (TITLE, TITLE)], capture_output=True, text=True, timeout=350)
    note = r.stdout
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
    new = [l for l in lines if l not in known and l not in headings and l not in heads and not l.startswith(("Staging:", "Live:"))]
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
        else
            make new note at default folder with properties {body:theBody}
            return "created"
        end if
    end tell
end tell
end timeout
'''


def main():
    import sys
    gone, new = pull()
    if "--pull" in sys.argv:
        return
    if new:
        print("New lines in the note are not in the master list yet: add them to private/archive-checklist.txt first (Claude does this), then run again.")
        return
    phases = parse()
    body = build_html(phases)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(body)
        path = f.name
    r = subprocess.run(["osascript", "-e", SCRIPT % (path, TITLE, TITLE)], capture_output=True, text=True, timeout=650)
    print((r.stdout or r.stderr).strip())
    if r.returncode == 0:
        SNAPSHOT.write_text(json.dumps(sorted(item_texts(phases))))
    Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
