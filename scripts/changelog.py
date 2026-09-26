#!/usr/bin/env python3
"""Numbered history of every push to the live site (Colin, 26 Sep 2026): 001 is the first push live, the newest is at the top of the note "Archive: Changelog".

The log is private/push-log.json ([{n, when, commit, snapshot, note}]): each entry names the commit that went live and the rollback snapshot (a zip of exactly what was
copied, in the private repo techspressionism-archive-snapshots). The changes listed for a push are the commits made since the previous push, read from git.

    python3 scripts/changelog.py add <commit> <snapshot tag> ["a note"]     # scripts/push-live.sh does this after every live copy
    python3 scripts/changelog.py show                                        # print the note text
To go back to push N: scripts/revert-live.sh N   (rehearsal first; --go copies)
"""
import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "private" / "push-log.json"
MAX_LINES = 22


def load():
    return json.loads(LOG.read_text()) if LOG.exists() else []


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def changes(prev, cur):
    subj = git("log", "--no-merges", "--format=%s", f"{prev}..{cur}" if prev else cur).splitlines()
    return [s[:110] for s in subj if s.strip()]


def add(commit, snapshot, note=""):
    log = load()
    short = git("rev-parse", "--short", commit)
    if log and log[-1]["commit"] == short:
        log[-1].update({"snapshot": snapshot, "note": note or log[-1].get("note", "")})
    else:
        log.append({"n": len(log) + 1, "when": datetime.now().strftime("%Y-%m-%d %H:%M"), "commit": short, "snapshot": snapshot, "note": note})
    LOG.write_text(json.dumps(log, indent=1))
    return log[-1]


def render_html():
    e = html.escape
    log = load()
    out = ["<h1>Archive: Changelog</h1>"]
    out.append("<p>Every push to the live site, newest first; 001 is the first push live. Each push lists what changed since the one before it. "
               "To go back to an earlier push, say \"revert live to 00N\" (N = the push number): I restore that push's snapshot, rehearse it, and it goes live once you run the copy.</p>")
    for k, ent in enumerate(reversed(log)):
        prev = log[len(log) - 2 - k]["commit"] if len(log) - 2 - k >= 0 else None
        try:
            d = datetime.strptime(ent["when"], "%Y-%m-%d %H:%M").strftime("%d %b %Y, %H:%M")
        except ValueError:
            d = ent["when"]
        out.append(f"<h2>Push {ent['n']:03d} - {e(d)}</h2>")
        out.append(f"<p>Commit {e(ent['commit'])}. Rollback snapshot: {e(ent['snapshot'])}." + (f" {e(ent['note'])}" if ent.get("note") else "") + "</p>")
        ch = changes(prev, ent["commit"]) if prev else []
        if not prev:
            ch = ["The first production copy of the archive (147 recordings, transcripts, search, artist pages) at techspressionism.com/archive, still hidden from search engines."]
        shown = ch[:MAX_LINES]
        more = len(ch) - len(shown)
        out.append("<ul>" + "".join(f"<li>{e(c)}</li>" for c in shown) + (f"<li>... and {more} smaller changes</li>" if more > 0 else "") + "</ul>")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "add":
        print(add(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else ""))
    else:
        print(render_html())
