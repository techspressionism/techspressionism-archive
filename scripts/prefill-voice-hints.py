#!/usr/bin/env python3
"""Pre-fill NameReview with a suggested name for the two main voices of each interview.

    python3 scripts/prefill-voice-hints.py            # every interview that has been through 03d-diarize.py
    python3 scripts/prefill-voice-hints.py --undo     # take the hints out again
    python3 scripts/prefill-voice-hints.py --confirm-two-voice   # also confirm the roles of two-voice interviews (see below)

In an interview there are usually two main voices, the interviewer and the interviewee. The voice that speaks most is guessed to be the
interviewee (the recording's interviewee in data/sessions.json) and the next one the interviewer. The names go in as a CANDIDATE
(tier "single", the reason says it is a guess) so NameReview shows them pre-filled in the name box; nothing is confirmed, and Stage 5
uses only confirmed or approved names. A person still listens and presses Confirm. Voices beyond the top two get no hint.

--confirm-two-voice: an interview with EXACTLY two separated voices, the louder speaking at least 1.25 times as long as the other, gets both roles
confirmed and its recording approved in NameReview (data/voice-names/<slug>.json approved_auto, which the recording's approve box shows and can
undo). Basis (20 September 2026): the guess agreed with the independent evidence (self-introductions and Colin's voice match) in 29 of 29 cases
and with Zoom's labels in 3 of 3; no two-voice interview has a ratio below 1.3. Interviews with more voices are left for a person: the extra
voices are the interviewer split in two, or extra people.
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
    confirmed = []
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
        ranked_secs = sorted(secs.values(), reverse=True)
        if "--confirm-two-voice" in sys.argv and not undo and path.stem.startswith("interview-") and len(d["voices"]) == 2 and len(ranked_secs) == 2 \
                and ranked_secs[0] >= 1.25 * ranked_secs[1] and all(i.get("candidate") for i in d["voices"].values()):
            dec_path = ROOT / "data" / "voice-names" / f"{path.stem}.json"
            if not dec_path.exists():                       # never overrides a person's own decisions
                for info in d["voices"].values():
                    if not info.get("screen") and not info.get("index"):
                        info.update({"name": info["candidate"], "tier": "confirmed",
                                     "why": "2-voice interview: the louder voice is the interviewee, the other the interviewer (32 of 32 checks); " + info["why"]})
                dec_path.parent.mkdir(parents=True, exist_ok=True)
                dec_path.write_text(json.dumps({"approved_auto": True, "voices": {}}, indent=1))
                confirmed.append(path.stem)
        path.write_text(json.dumps(d, indent=1, ensure_ascii=False))
        changed += 1
    print(f"{'removed hints from' if undo else 'hints set on'} the interviews; speaking times filled in on {changed} recordings"
          + (f"; {len(confirmed)} two-voice interviews confirmed: {', '.join(confirmed)}" if confirmed else ""))


if __name__ == "__main__":
    main()
