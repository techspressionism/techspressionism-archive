"""Canonical speaker names for the corpus (headings, citations, filter).

Rules live in data/speaker_aliases.json; the generic cleanups here handle the
long tail (stray leading numbers, trailing parentheticals, ALL-CAPS).
"""
import json
import re
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "data" / "speaker_aliases.json"
_cfg = json.loads(_PATH.read_text())
_norm = lambda s: re.sub(r"\s+", " ", s or "").strip()
ALIASES = {_norm(k).lower(): v for k, v in _cfg["aliases"].items()}
NOT_SPEAKERS = {_norm(k).lower() for k in _cfg["not_speakers"]}
DUOS = {_norm(k).lower(): v for k, v in _cfg["duos"].items()}

_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")


def is_not_speaker(raw):
    return _norm(raw).lower() in NOT_SPEAKERS


def canonical_name(raw):
    """One display name for a raw speaker label. Non-speaker labels
    (segment titles like 'COLLAB #1') and duos are returned untouched apart
    from whitespace/trailing-parenthetical cleanup."""
    n = _norm(raw)
    if not n or is_not_speaker(n):
        return n
    n = re.sub(r"^\d+\s+(?=[^\W\d])", "", n)
    if n.lower() in ALIASES:
        return ALIASES[n.lower()]
    stripped = n
    while _TRAILING_PAREN.search(stripped):
        stripped = _TRAILING_PAREN.sub("", stripped)
    n = stripped or n
    if n.lower() in ALIASES:
        return ALIASES[n.lower()]
    if any(c.isalpha() for c in n) and n == n.upper():
        n = n.title()
    return ALIASES.get(n.lower(), n)


def trailing_location(raw):
    """A '(City, ST USA)' suffix some speaker labels carry."""
    m = _TRAILING_PAREN.search(_norm(raw))
    return m.group().strip()[1:-1] if m and "USA" in m.group() else None


def finalize_speakers(speakers):
    """Session speaker list -> one entry per person: canonical names, duos
    split into their members, non-speaker labels dropped, duplicates merged
    (earliest start wins, missing location filled in from later entries)."""
    out, by_name = [], {}
    for s in sorted(speakers, key=lambda s: s["start_seconds"]):
        raw = _norm(s["name"])
        if is_not_speaker(raw):
            continue
        names = DUOS.get(raw.lower()) or [canonical_name(raw)]
        loc = s.get("country") or trailing_location(raw)
        for name in names:
            if name in by_name:
                if not by_name[name].get("country") and loc:
                    by_name[name]["country"] = loc
                continue
            entry = {**s, "name": name, "country": loc}
            by_name[name] = entry
            out.append(entry)
    return out
