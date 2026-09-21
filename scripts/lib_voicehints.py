"""Combine the automatic hints about who a separated voice is (used by intro-hints.py, voice-match.py, prefill-voice-hints.py).

Each hint source stores {"name", "why"} under info["hints"][source]. resolve(info) then sets what NameReview shows:
  * two independent STRONG sources ("intro": the person says who they are; "voice": their enrolled voice matches) that name the same person
    -> tier "confirmed", name = that person ("2 sources agree", the same standard as Nametag + speaker index). It is still only used after a
    person ticks "Approve the automatically confirmed names" for the recording in NameReview, or confirms the voice.
  * one strong source, or only the weak "role" guess (interviewees speak most) -> a candidate (tier "single") pre-selected for a person to confirm.
  * two strong sources naming different people -> tier "conflict", no candidate, the reasons listed.
A voice that Nametag / the speaker index already named (info has "screen" or "index") is left alone.
"""
STRONG = ("intro", "voice")
LABEL = {"intro": "introduces themselves", "voice": "voice match", "role": "guess"}


def resolve(info):
    if info.get("screen") or info.get("index") or info.get("name") and not info.get("hints"):
        return
    hints = {k: v for k, v in (info.get("hints") or {}).items() if v and v.get("name")}
    strong = {k: v for k, v in hints.items() if k in STRONG}
    names = {v["name"] for v in strong.values()}
    if len(names) > 1:
        info.update({"candidate": None, "name": None, "tier": "conflict", "why": "hints disagree: " + "; ".join(f"{LABEL[k]}: {v['name']}" for k, v in strong.items())})
    elif len(strong) >= 2:
        name = next(iter(names))
        info.update({"candidate": name, "name": name, "tier": "confirmed", "why": "2 sources agree on " + name + ": " + "; ".join(f"{LABEL[k]} ({v['why']})" for k, v in strong.items())})
    elif strong:
        k, v = next(iter(strong.items()))
        info.update({"candidate": v["name"], "name": None, "tier": "single", "why": f"{LABEL[k]}: {v['why']}"})
    elif hints.get("role"):
        v = hints["role"]
        info.update({"candidate": v["name"], "name": None, "tier": "single", "why": v["why"]})
    else:
        info.update({"candidate": None, "name": None, "tier": "none", "why": "no Nametag readings for this recording"})


def set_hint(info, source, name, why):
    hints = info.setdefault("hints", {})
    if name:
        hints[source] = {"name": name, "why": why}
    else:
        hints.pop(source, None)


_DIRECTORY = {}


def _key(name):
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)


def directory_name(name):
    """The people directory's spelling of `name` when it is exactly a directory artist's full name or alias (ignoring capitals, accents and
    punctuation), else None. Colin, 20 September 2026: an on-screen Zoom name that matches a directory artist always means that person."""
    if not _DIRECTORY:
        import json
        import re
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "data" / "people.json"
        for p in json.loads(path.read_text())["people"]:
            for n in [p["name"]] + p.get("aliases", []):
                n2 = re.sub(r"\s*\([^)]*\)", "", n).split(" aka ")[0].strip()
                if len(n2.split()) >= 2 and "/" not in n2:
                    _DIRECTORY.setdefault(_key(n2), p["name"])
    return _DIRECTORY.get(_key(name))


_WORDS = set()


def distinctive_handle(name):
    """True for a one-word artist name (a handle such as ScoJo) that can be looked for in text: letters only, five or more of them, and
    not an ordinary English word (checked against the system dictionary), so 'Scojo' matches and 'meta' or 'regie' do not."""
    import re
    n = (name or "").strip()
    if not re.fullmatch(r"[A-Za-z]{5,}", n):
        return False
    if not _WORDS:
        try:
            _WORDS.update(w.strip().lower() for w in open("/usr/share/dict/words", encoding="utf8", errors="ignore"))
        except OSError:
            _WORDS.add("")
    return n.lower() not in _WORDS
