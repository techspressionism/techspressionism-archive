#!/usr/bin/env python3
"""Pre-fill NameReview with a suggested name for the two main voices of each interview.

    python3 scripts/prefill-voice-hints.py            # every interview that has been through 03d-diarize.py
    python3 scripts/prefill-voice-hints.py --undo     # take the hints out again

In an interview there are usually two main voices, the interviewer and the interviewee. The voice that speaks most is guessed to be the
interviewee (the recording's interviewee in data/sessions.json) and the next one the interviewer. The names go in as a CANDIDATE
(tier "single", the reason says it is a guess) so NameReview shows them pre-filled in the name box; nothing is confirmed, and Stage 5
uses only confirmed or approved names. A person still listens and presses Confirm. Voices beyond the top two get no hint.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_voicehints import resolve, set_hint  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WHY = {"interviewee": "guess: in an interview the voice that speaks most is usually the interviewee",
       "interviewer": "guess: in an interview the voice that speaks second most is usually the interviewer"}


def main():
    undo = "--undo" in sys.argv
    sessions = {f"{s.get('type', 'salon')}-{int(s['number']):03d}": s for s in json.loads((ROOT / "data" / "sessions.json").read_text())}
    changed = 0
    for path in sorted((ROOT / "raw" / "diarize").glob("*.json")):
        d = json.loads(path.read_text())
        s = sessions.get(path.stem, {})
        secs = {}
        for a, b, v in d.get("turns", []):
            secs[v] = secs.get(v, 0) + (b - a)
        ranked = [v for v, _ in sorted(secs.items(), key=lambda kv: -kv[1])]
        for v, info in d["voices"].items():                # how long each voice speaks (the review page shows it and sorts by it)
            if not info.get("seconds"):
                info["seconds"] = round(secs.get(v, 0), 1)
        for info in d["voices"].values():
            set_hint(info, "role", None, "")
        if not undo and path.stem.startswith("interview-"):
            for role, name, voice in (("interviewee", s.get("interviewee"), ranked[0] if ranked else None),
                                      ("interviewer", s.get("interviewer"), ranked[1] if len(ranked) > 1 else None)):
                if name and voice:
                    set_hint(d["voices"][voice], "role", name, WHY[role])
        for info in d["voices"].values():
            resolve(info)
        path.write_text(json.dumps(d, indent=1, ensure_ascii=False))
        changed += 1
    print(f"{'removed hints from' if undo else 'hints set on'} the interviews; speaking times filled in on {changed} recordings")


if __name__ == "__main__":
    main()
