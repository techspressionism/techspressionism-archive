#!/usr/bin/env python3
"""Write the archive's open items into the macOS Notes note "Techspressionism Archive To Do List" (iCloud, so it shows on every device).

Source: private/archive-checklist.txt (the master checklist). The note has the two archive links under the title, then for each phase its
open items followed by the recommendations. Done items are left out. Re-running replaces the note's text; it creates the note if it is missing.

    python3 scripts/update-notes-checklist.py
"""
import html
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "private" / "archive-checklist.txt"
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
    body = build_html(parse())
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(body)
        path = f.name
    r = subprocess.run(["osascript", "-e", SCRIPT % (path, TITLE, TITLE)], capture_output=True, text=True, timeout=650)
    print((r.stdout or r.stderr).strip())
    Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
