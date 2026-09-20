#!/usr/bin/env python3
"""Stage 5 -- assemble corpus/salon-NNN.md (+ corpus/corpus.json).

Two very different inputs depending on transcript_source:

- zoom-transcript: raw/transcripts_corrected/salon-NNN.json cues already
  carry per-utterance speaker attribution and Stage 4 corrections. Just
  normalize Zoom's raw speaker labels against Stage 1's speaker index /
  the artist index, then group consecutive same-speaker cues into blocks.

- youtube-*-captions: the Stage 2 output has no speaker structure at all
  (one flat word-timestamp stream). Slice it into per-speaker segments
  using Stage 1's manual speaker-index timestamps FIRST, then run name
  correction on each resulting segment -- deliberately not reusing Stage
  4's whole-transcript output here, since correcting the flat blob before
  slicing would leave word-level timestamps out of sync with text whose
  length Stage 4's substitutions may have changed.

Usage:
    python3 scripts/05-build-corpus.py 20 48 109
"""
import json
import re
import sys
from bisect import bisect_right
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from lib_corrections import apply_style_rules, apply_vocabulary, correct_text
from lib_media import TYPES, label, selected, slug
from lib_sentences import sentence_times
from lib_speakers import canonical_name, finalize_speakers, is_not_speaker

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_PATH = ROOT / "data" / "sessions.json"
ARTISTS_PATH = ROOT / "data" / "artists.json"
VOCAB_PATH = ROOT / "data" / "vocabulary.json"
TRANSCRIPTS_DIR = ROOT / "raw" / "transcripts"
CORRECTED_DIR = ROOT / "raw" / "transcripts_corrected"
CORPUS_DIR = ROOT / "corpus"
REVIEW_CSV = ROOT / "review" / "name-candidates.csv"

NORMALIZE_THRESHOLD = 0.6
AUDIO_SHARED_PREFIX = "Audio shared by "

# Raw YouTube-caption ASR has no punctuation at all. There's no punctuation-
# restoration model available locally, so this is a heuristic: a pause
# between words is a reasonable proxy for a sentence or paragraph break (the
# same signal captioning tools use). It won't pick "!" or "?" over ".", and
# it adds no commas -- pause timing alone is too noisy a signal for comma
# placement, so mid-sentence punctuation is deliberately left to whatever
# the name-correction pass already does.
SENTENCE_PAUSE_SECONDS = 1.3
PARAGRAPH_PAUSE_SECONDS = 3.0


def restore_punctuation(words):
    """words: ordered list of {"start": seconds, "text": word}. Returns
    (punctuated, paragraphed, sentence-capitalized text, the start time of each paragraph)."""
    if not words:
        return "", []
    out = []
    starts = [words[0]["start"]]
    capitalize_next = True
    for i, w in enumerate(words):
        text = w["text"]
        if capitalize_next and text:
            text = text[0].upper() + text[1:]
            capitalize_next = False
        if out and not out[-1].endswith("\n\n"):
            out.append(" ")
        out.append(text)
        if i + 1 < len(words):
            gap = words[i + 1]["start"] - w["start"]
            if gap >= PARAGRAPH_PAUSE_SECONDS:
                out.append(".\n\n")
                starts.append(words[i + 1]["start"])
                capitalize_next = True
            elif gap >= SENTENCE_PAUSE_SECONDS:
                out.append(".")
                capitalize_next = True
    if out and not out[-1].rstrip().endswith((".", "!", "?", "\n\n")):
        out.append(".")
    return "".join(out), starts


# Paragraphs follow the speaker's thought: a new paragraph starts at the end of a sentence
# where the speaker pauses and moves on, once the paragraph has some body. A paragraph never
# runs past PARAGRAPH_MAX_CHARS: it is then cut at the next sentence end whatever the pause.
PARAGRAPH_SOFT_MIN_CHARS = 300
PARAGRAPH_MAX_CHARS = 800
THOUGHT_PAUSE_SECONDS = 1.3


def punctuate_subtitles(words):
    """Speech-recognition and human-made subtitle text already carries its punctuation and
    capitalization. Only paragraph it, at a sentence end: at a natural pause once the paragraph
    is long enough for a thought (300+ characters), or unconditionally past 800 characters.
    Returns (text, the start time of each paragraph)."""
    if not words:
        return "", []
    out, starts, para_len = [], [words[0]["start"]], 0
    for i, w in enumerate(words):
        out.append(w["text"])
        para_len += len(w["text"]) + 1
        if i + 1 < len(words):
            sentence_end = w["text"].rstrip("\"'”’)").endswith((".", "!", "?"))
            pause = words[i + 1]["start"] - w["start"]
            if sentence_end and ((para_len >= PARAGRAPH_SOFT_MIN_CHARS and pause >= THOUGHT_PAUSE_SECONDS)
                                 or para_len >= PARAGRAPH_MAX_CHARS):
                out.append("\n\n")
                starts.append(words[i + 1]["start"])
                para_len = 0
            else:
                out.append(" ")
    return "".join(out).replace(" \n\n", "\n\n").strip(), starts


def word_time_maps(words, text):
    """Per paragraph, [[fraction of the paragraph text, start time of the word there], ...], read off the finished
    paragraph text. Words map one-to-one onto the whitespace-separated tokens of the text; if that ever fails
    (token count differs), return None and the caller falls back to paragraph-level times."""
    paras = text.split("\n\n")
    tokens = [[m.start() for m in re.finditer(r"\S+", p)] for p in paras]
    if sum(len(x) for x in tokens) != len(words):
        return None
    maps, i = [], 0
    for p, offs in zip(paras, tokens):
        n = max(1, len(p))
        maps.append([[o / n, words[i + k]["start"]] for k, o in enumerate(offs)])
        i += len(offs)
    return maps


def cue_time_map(pieces):
    """Zoom paragraph: pieces = [(cue start, cue end, text length), ...]; time map at the cue boundaries."""
    total = sum(l for _s, _e, l in pieces) or 1
    tmap, cum = [], 0
    for s, e, l in pieces:
        tmap.append([cum / total, s])
        cum += l
        tmap.append([cum / total, e])
    return tmap


TIME_BLOCK_SECONDS = 180


SENTENCE_END = re.compile(r"""[.?!…]["'”’)\]]*$""")
ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "etc.", "e.g.", "i.e.", "no.", "jr.", "sr.", "prof."}


def ends_sentence(word):
    text = (word.get("text") or "").strip()
    return bool(SENTENCE_END.search(text)) and text.lower() not in ABBREVIATIONS


def time_block_starts(words, block=TIME_BLOCK_SECONDS, slack=90):
    """Start times that cut an unattributed transcript into ~3-minute blocks so every
    search hit can deep-link to a moment near where it was actually said. A block starts
    at the first SENTENCE start after the target (never in the middle of a sentence);
    text with no punctuation (YouTube auto-captions) falls back to the first natural pause
    (>= 0.8 s), and if neither turns up within the slack the block starts at the target."""
    starts = [words[0]["start"]]
    target = starts[0] + block
    i, n = 1, len(words)
    while i < n:
        if words[i]["start"] < target:
            i += 1
            continue
        cut, pause = None, None
        j = i
        while j < n and words[j]["start"] < target + slack:
            if cut is None and ends_sentence(words[j - 1]):
                cut = j
                break
            if pause is None and words[j]["start"] - words[j - 1]["start"] >= 0.8:
                pause = j
            j += 1
        if cut is None:
            cut = pause if pause is not None else i
        starts.append(words[cut]["start"])
        target = words[cut]["start"] + block
        i = cut + 1
    return starts


def build_known_terms(session, vocab_terms, artists):
    """Proper nouns safe to capitalize on sight, case-insensitive exact
    match, word-boundary-anchored. Deliberately excludes single-token names
    (handles like "cha", "meta", real-word names like a hypothetical "Art")
    -- those collide with ordinary vocabulary too easily to capitalize
    blindly; the fuzzy correction pass already handles them more carefully.
    Full multi-word names carry effectively no such risk (an exact "colin
    goldberg" match is never a coincidence), so unlike the fuzzy pool this
    draws on the whole artist index, not just this session's own speakers
    -- someone like Colin Goldberg gets mentioned constantly without being
    formally indexed as "speaking" in most sessions."""
    # Every name goes in as its CANONICAL spelling (data/speaker_aliases.json), so variants of one
    # person ("Susan DeTroy" from an index, "Susan Detroy" from the vocabulary) collapse to one term.
    # Without that, same-length variants were applied in set-iteration order -- which changes with
    # Python's per-run hash seed -- and the page came out differently from one build to the next.
    terms = set()
    for s in session.get("speakers", []):
        if len(s["name"].split()) >= 2:
            terms.add(canonical_name(s["name"]))
    if session.get("moderator") and len(session["moderator"].split()) >= 2:
        terms.add(canonical_name(session["moderator"]))
    for a in artists:
        if len(a["name"].split()) >= 2:
            terms.add(canonical_name(a["name"]))
    for t in vocab_terms:
        terms.add(t["canonical"])
    return sorted(terms, key=lambda t: (-len(t), t))   # longest first; ties alphabetical, never set order


def capitalize_known_terms(text, terms):
    for term in terms:
        # a lambda replacement is used verbatim -- re.sub's string-replacement
        # form treats a literal backslash-digit in `term` as a group
        # reference (this once crashed on a mangled artist-index entry)
        text = re.sub(rf"\b{re.escape(term)}\b", lambda m, t=term: t, text, flags=re.IGNORECASE)
    text = re.sub(r"\bi\b", "I", text)  # standalone pronoun
    text = re.sub(r"(^|[.!?\n]\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text


def seconds_to_display(total):
    if total is None:
        return "00:00"
    m, s = divmod(int(total), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum() or c == " ").strip()


COUNTRY_SUFFIXES = {
    "usa": "USA", "us": "USA", "u s a": "USA", "united states": "USA", "america": "USA",
    "uk": "United Kingdom", "u k": "United Kingdom", "united kingdom": "United Kingdom",
    "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom",
    "uae": "United Arab Emirates",
}

US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in",
    "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv",
    "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn",
    "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}


def build_country_resolver(artists):
    by_name = {}
    valid = set()
    for a in artists:
        if a.get("country"):
            by_name[_norm(a["name"])] = a["country"]
            valid.add(a["country"])
    valid_norm = {_norm(c): c for c in valid}

    def resolve(name, raw_location):
        hit = by_name.get(_norm(name))
        if hit:
            return hit
        loc = _norm(raw_location)
        if not loc:
            return None
        for suffix, canon in COUNTRY_SUFFIXES.items():
            if loc == suffix or loc.endswith(" " + suffix):
                return canon
        for cn, canon in valid_norm.items():
            if loc == cn or loc.endswith(" " + cn):
                return canon
        if loc.split()[-1] in US_STATE_CODES:  # "Brooklyn NY", "Astoria, NY"
            return "USA"
        return None

    return resolve


def normalize_speaker_name(raw_name, session_speakers, artists):
    """Zoom's own speaker label is the participant's self-identification --
    not ASR output -- so it's already high-confidence and should mostly be
    left alone. Only normalize against the session's OWN Stage 1 speaker
    list (a small, scoped, strong prior: we know exactly who's presenting
    in this specific session). Do NOT fall back to fuzzy-matching against
    the full ~460-person artist index: that pool is unscoped, and two
    different real people can easily share enough of a name to collide
    (this once turned the real "Lisa Sutton" into a wrong match on the
    unrelated real artist "Lisa Scadron" -- a misattribution, not a typo
    fix, in a citable archive)."""
    if raw_name.startswith(AUDIO_SHARED_PREFIX):
        raw_name = raw_name[len(AUDIO_SHARED_PREFIX):]

    best = (0.0, None)
    for name in [s["name"] for s in session_speakers]:
        ratio = SequenceMatcher(None, raw_name.lower(), name.lower()).ratio()
        if raw_name.lower() in name.lower() or name.lower() in raw_name.lower():
            ratio = max(ratio, 0.75)
        if ratio > best[0]:
            best = (ratio, name)

    return best[1] if best[0] >= NORMALIZE_THRESHOLD else raw_name


def yaml_scalar(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if any(c in s for c in ':"#[]{}') or s != s.strip():
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def build_frontmatter(session, segments):
    lines = ["---"]
    lines.append(f"type: {session['type']}")
    lines.append(f"number: {session['number']}")
    lines.append(f"title: {yaml_scalar(session['session_title'])}")
    lines.append(f"date_recorded: {session['date_recorded'] or 'null'}")
    lines.append(f"date_published: {session['date_published'] or 'null'}")
    lines.append(f"video_id: {yaml_scalar(session['video_id'])}")
    lines.append(f"url: {yaml_scalar(session['url'])}")
    lines.append(f"duration_seconds: {session['duration_seconds']}")
    lines.append(f"moderator: {yaml_scalar(session['moderator'])}")
    for key in ("interviewee", "interviewer"):
        if session.get(key):
            lines.append(f"{key}: {yaml_scalar(session[key])}")
    lines.append("speakers:")
    for s in session.get("speakers", []):
        lines.append(f"  - name: {yaml_scalar(s['name'])}")
        lines.append(f"    country: {yaml_scalar(s['country'])}")
        lines.append(f"    start: {s.get('start_seconds')}")
    lines.append(f"transcript_source: {yaml_scalar(session['transcript_source'])}")
    lines.append('languages: ["en"]')
    if session.get("flags"):
        lines.append("flags:")
        for flag in session["flags"]:
            lines.append(f"  - {flag}")
    lines.append("---")
    return "\n".join(lines)


TEXT_EDITS_DIR = ROOT / "data" / "text-edits"


def apply_text_edits(session, segments):
    """Corrections made by people (TextReview, or suggestions imported from the public form),
    stored apart from the machine text in data/text-edits/<slug>.json so a rebuild never loses them.
    An edit says: the paragraph that reads `old`, near time `t`, should read `new`. It is applied
    only where `old` matches a paragraph EXACTLY; if the machine text has changed since (a re-run of
    Whisper, a new vocabulary rule) the edit is reported as stale and left unapplied rather than
    guessed at. Only edits with status "approved" are used. Runs last, after every other processing
    step, so the person's words are published as written."""
    path = TEXT_EDITS_DIR / f"{slug(session)}.json"
    if not path.exists():
        return segments
    edits = [e for e in json.loads(path.read_text()).get("edits", []) if e.get("status", "approved") == "approved"]
    if not edits:
        return segments
    paragraphs = [seg["text"].split("\n\n") for seg in segments]
    applied = stale = 0
    for e in edits:
        hits = []
        for i, paras in enumerate(paragraphs):
            for j, p in enumerate(paras):
                if p == e["old"]:
                    start = segments[i]["start"] or 0
                    end = segments[i].get("end") or start
                    hits.append((0 if start <= e["t"] <= end else min(abs(e["t"] - start), abs(e["t"] - end)), i, j))
        if not hits:
            stale += 1
            continue
        _, i, j = min(hits)
        paragraphs[i][j] = e["new"]
        applied += 1
    for seg, paras in zip(segments, paragraphs):
        seg["text"] = "\n\n".join(paras)
    print(f"  {label(session)}: {applied} text corrections applied" + (f", {stale} STALE (the machine text changed; review them in TextReview)" if stale else ""))
    return segments


def build_body(session, segments):
    parts = []
    for seg in segments:
        ts = seconds_to_display(seg["start"])
        link = f"{session['url']}&t={int(seg['start'])}s" if seg["start"] is not None else session["url"]
        speaker = seg["speaker"] or "Unattributed"
        parts.append(f"## {speaker} [{ts}]({link})\n\n{seg['text']}")
    return "\n\n".join(parts)


# Words Zoom capitalizes as a matter of course at the start of every cue,
# regardless of whether that cue actually starts a new sentence. Safe to
# lowercase only when we've decided the cue is a plain-space continuation
# of the previous one (no gap-based punctuation inserted) -- a small,
# closed set of pronouns/conjunctions that are never themselves proper
# nouns, so there's no risk of clobbering a genuine capitalized word.
ZOOM_PARAGRAPH_MIN_CHARS = 450

TITLES = json.loads((ROOT / "data" / "titles.json").read_text())["titles"]

CONTINUATION_LOWERCASE_WORDS = {
    "he", "she", "it", "they", "we", "you", "and", "but", "so", "then",
    "there", "here", "that", "this", "who", "which", "because", "if",
    "when", "while",
}


def fix_continuation_capitalization(text):
    m = re.match(r"([A-Z])([a-z']*)\b", text)
    if not m:
        return text
    word = (m.group(1) + m.group(2)).lower()
    if word in CONTINUATION_LOWERCASE_WORDS:
        return m.group(1).lower() + text[1:]
    return text


DECADE_DIGITS = {
    "twenties": "20", "thirties": "30", "forties": "40", "fifties": "50",
    "sixties": "60", "seventies": "70", "eighties": "80", "nineties": "90",
}
_DECADE_WORDS = "|".join(DECADE_DIGITS)


def fix_spoken_decades(text):
    """ASR renders "nineteen sixties" as "19 sixties". Unlike a bare year,
    this shape has exactly one meaning, so a general rule is safe. A second
    decade directly joined to the first is shortened ("1960s and 70s")."""
    text = re.sub(rf"\b19 ({_DECADE_WORDS})\b", lambda m: f"19{DECADE_DIGITS[m.group(1).lower()]}s", text, flags=re.I)
    text = re.sub(r"\b19 hundreds\b", "1900s", text, flags=re.I)
    # "1960s and seventies" / "1960s and 1970s" -> "1960s and 70s"
    text = re.sub(
        rf"\b(19\d0s)( and | or | to | through )(?:19(\d0)s|({_DECADE_WORDS}))\b",
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3) or DECADE_DIGITS[m.group(4).lower()]}s",
        text, flags=re.I)
    return text


TITLES_PATH = ROOT / "data" / "titles.json"
TITLE_HITS = {}


def italicize_titles(text):
    """Wrap hand-verified book titles in _underscores_ (Stage 6 renders them
    as italics). Exact-phrase matching only -- guessing where a title ends
    from context alone is how false italics creep in."""
    for entry in TITLES:
        text, n = re.subn(re.escape(entry["find"]), lambda m, r=entry["replace"]: r, text, flags=re.I)
        TITLE_HITS[entry["find"]] = TITLE_HITS.get(entry["find"], 0) + n
    return text


def segments_from_zoom(session, artists, vocab_terms):
    path = CORRECTED_DIR / f"{slug(session)}.json"
    with open(path) as f:
        data = json.load(f)

    known_terms = build_known_terms(session, vocab_terms, artists)
    segments = []
    for cue in data["cues"]:
        speaker = normalize_speaker_name(cue["speaker"], session.get("speakers", []), artists) if cue["speaker"] else None
        text = capitalize_known_terms(cue["text"], known_terms)
        if segments and segments[-1]["speaker"] == speaker:
            # Zoom's cue boundaries are its own internal processing chunks,
            # NOT reliable pause/paragraph markers -- consecutive cues are
            # sometimes 0.1s apart, cut off mid-sentence with no terminal
            # punctuation. Only insert a period/paragraph break where the
            # actual gap suggests a real pause (same two thresholds as the
            # YouTube pause-based path); a near-zero gap just continues the
            # sentence exactly as-is, even if the cue text alone looks
            # unpunctuated -- inventing a period there is worse than leaving
            # one out.
            gap = cue["start"] - segments[-1]["end"]
            prev = segments[-1]["text"]
            needs_period = not prev.rstrip().endswith((".", "!", "?"))
            paragraph_len = len(prev) - (prev.rfind("\n\n") + 2 if "\n\n" in prev else 0)
            if gap >= PARAGRAPH_PAUSE_SECONDS:
                joiner = (".\n\n" if needs_period else "\n\n")
            elif gap >= SENTENCE_PAUSE_SECONDS:
                joiner = (". " if needs_period else " ")
            elif not needs_period and paragraph_len >= ZOOM_PARAGRAPH_MIN_CHARS:
                # Zoom cues are near-contiguous, so pauses never trigger a
                # break in a fluent monologue; fall back to length, but only
                # at a real sentence end so we never split mid-sentence.
                joiner = "\n\n"
            else:
                joiner = " "
                if needs_period:
                    text = fix_continuation_capitalization(text)
            segments[-1]["text"] += joiner + text
            segments[-1]["end"] = cue["end"]
            if "\n\n" in joiner:
                segments[-1]["para_starts"].append(cue["start"])
                segments[-1]["para_pieces"].append([(cue["start"], cue["end"], len(text))])
            else:
                segments[-1]["para_pieces"][-1].append((cue["start"], cue["end"], len(joiner) + len(text)))
        else:
            segments.append({"speaker": speaker, "start": cue["start"], "end": cue["end"], "text": text,
                             "para_starts": [cue["start"]], "para_pieces": [[(cue["start"], cue["end"], len(text))]]})

    for seg in segments:
        seg["text"], _ = apply_vocabulary(seg["text"], vocab_terms)
    return segments


NAMETAG_DIR = ROOT / "raw" / "nametag"
NAMETAG_LAG = 1.0       # Zoom moves the on-screen name about a second after a voice starts
NAMETAG_MIN_RUN = 6.0   # a shorter blip of another name between two runs of one person is label flicker
NAMETAG_SNAP = 4.0      # a change of speaker moves to a sentence end/pause this close to it
NAMETAG_FILL = 10.0     # seconds a name carries through a gap with no label on screen


def _speaker_name(text):
    """A person's display name from the on-screen text, or None. Rejects text from
    a shared screen ("Publish to Hubs...", "• Share") and strips OCR stray marks."""
    text = re.sub(r"^[^A-Za-z]+|[^A-Za-z.]+$", "", text or "").strip()
    ok = re.fullmatch(r"[A-Za-z][A-Za-z.'’\- ]{2,40}", text) and 1 <= len(text.split()) <= 4
    # whole words only: a substring test rejects real names ("Christopher" contains "stop")
    ui = r"\b(share[ds]?|publish\w*|favorites?|screen|zoom|record\w*|mute\w*|stop|bookmarks?|other|hubs?|desktop|downloads?|folders?|windows?|menu|chrome|safari|finder|home)\b"
    return text if ok and not re.search(ui, text, re.I) else None


def nametag_boundaries(session, words, artists):
    """[(start_seconds, raw speaker name)] for a Whisper transcript, from the
    on-screen speaker names Stage 3c read off the video -- or None when there
    is no usable reading. Words are attributed by the name showing a moment
    after they were said, flicker is smoothed, and each change of speaker is
    moved to the nearest sentence end so a turn never starts mid-sentence.
    Turns render like every other attributed page: one heading per turn."""
    path = NAMETAG_DIR / f"{slug(session)}.json"
    if session.get("transcript_source") != "whisper-large-v3" or not path.exists():
        return None
    tag = json.loads(path.read_text())
    # "usable" only means names could be read; a recording is used for attribution
    # only after it has been checked and marked "approved": true (the survey found
    # spotlighted cameras and screen text that read fine but do not name the speaker)
    if not tag.get("usable") or tag.get("partial") or not tag.get("approved"):
        return None
    step, samples = tag["step"], tag["samples"]

    # Variants of one person's name -> the most common spelling: spacing and
    # punctuation ("Systaime M B" / "Systaime MB", "•Yuge Zhou") and the text
    # reader's near-misses ("Jan Swinbume" / "Jan Swinburne")
    samples = [[t, _speaker_name(n), lay] for t, n, lay in samples]  # screen text -> None
    spellings = []  # (letters only, spelling), most common first
    spelling = {}
    for name, _ in Counter(n for _, n, _ in samples if n).most_common():
        key = re.sub(r"[^a-z]", "", name.lower())
        hit = next((s for s in spellings if SequenceMatcher(None, key, s[0]).ratio() >= 0.85), None)
        if not hit:
            spellings.append((key, name))
            hit = spellings[-1]
        spelling[name] = hit[1]
    labels = [normalize_speaker_name(spelling[n], session.get("speakers", []), artists) if n else None
              for _, n, _ in samples]
    # No name on screen (a pause, a moment of gallery view): whoever was last named
    # keeps the floor, but only for NAMETAG_FILL seconds. A longer gap stays
    # unattributed -- guessing through it credits words to the wrong person.
    last, since = None, 0
    for i, n in enumerate(labels):
        if n:
            last, since = n, 0
        else:
            since += step
            labels[i] = last if since <= NAMETAG_FILL else None
    # A-B-A with a short B is flicker, not a turn
    runs, i = [], 0
    while i < len(labels):
        j = i
        while j < len(labels) and labels[j] == labels[i]:
            j += 1
        runs.append([labels[i], i, j])
        i = j
    for k in range(1, len(runs) - 1):
        if runs[k - 1][0] == runs[k + 1][0] != runs[k][0] and (runs[k][2] - runs[k][1]) * step < NAMETAG_MIN_RUN:
            for x in range(runs[k][1], runs[k][2]):
                labels[x] = runs[k - 1][0]

    def label_at(t):
        return labels[min(max(round((t + NAMETAG_LAG - samples[0][0]) / step), 0), len(labels) - 1)]

    def turn_start(j):  # would a new turn read naturally starting at word j?
        return j == 0 or words[j - 1]["text"].rstrip("\"'”’)").endswith((".", "!", "?")) or words[j]["start"] - words[j - 1]["start"] >= 1.0

    per_word = [label_at(w["start"]) for w in words]
    changes = [i for i in range(1, len(words)) if per_word[i] != per_word[i - 1]]
    cuts = []
    for c in changes:
        near = [j for j in range(max(1, c - 40), min(len(words), c + 40)) if turn_start(j) and abs(words[j]["start"] - words[c]["start"]) <= NAMETAG_SNAP]
        cuts.append(min(near, key=lambda j: abs(words[j]["start"] - words[c]["start"])) if near else c)
    starts = [0] + sorted(set(cuts))
    result = []
    for a, b in zip(starts, starts[1:] + [len(words)]):
        # the speaker of a turn is whoever the labels say for most of its words
        who = max({p: per_word[a:b].count(p) for p in set(per_word[a:b])}.items(), key=lambda kv: kv[1])[0]
        if result and result[-1][1] == who:
            continue
        result.append((words[a]["start"], who))
    return result


DIARIZE_DIR = ROOT / "raw" / "diarize"
VOICE_NAMES_DIR = ROOT / "data" / "voice-names"


def _voice_fingerprint(turns, voice):
    """Same as namereview.py: changes if the voices were re-separated, so an old decision is
    never applied to a different voice."""
    import hashlib
    mine = [(round(s), round(e)) for s, e, v in turns if v == voice]
    return hashlib.sha1(json.dumps(mine[:40]).encode()).hexdigest()[:10]


def voice_boundaries(session, words):
    """[(start_seconds, name or None)] for a Whisper transcript, from the voices Stage 3d
    separated and the names a person confirmed in NameReview (data/voice-names/<slug>.json).
    A voice gets a name only from a current human decision, or -- when the reviewer ticked
    "approve the automatic names" -- from its 'confirmed' name (screen and speaker index agreed).
    Every other voice stays unattributed. None when there is nothing to use."""
    d_path, n_path = DIARIZE_DIR / f"{slug(session)}.json", VOICE_NAMES_DIR / f"{slug(session)}.json"
    if session.get("transcript_source") != "whisper-large-v3" or not d_path.exists() or not n_path.exists():
        return None
    diar, dec = json.loads(d_path.read_text()), json.loads(n_path.read_text())
    turns = sorted(tuple(t) for t in diar["turns"])
    names = {}
    for voice, info in diar["voices"].items():
        saved = dec.get("voices", {}).get(voice)
        if saved is not None and saved.get("fingerprint") == _voice_fingerprint(turns, voice):
            names[voice] = saved.get("name") or None          # "" = the reviewer chose to leave it unattributed
        elif dec.get("approved_auto") and info.get("tier") == "confirmed":
            names[voice] = info["name"]
    if not any(names.values()):
        return None
    starts = [t[0] for t in turns]

    def voice_at(t0):
        """The voice speaking during [t0, t0+0.35]: most overlap, else the nearest turn within 1.5 s."""
        i, best = bisect_right(starts, t0) - 1, None
        for k in range(max(i - 25, 0), min(i + 6, len(turns))):
            s, e, v = turns[k]
            ov = min(e, t0 + 0.35) - max(s, t0)
            gap = 0 if ov > 0 else min(abs(s - (t0 + 0.35)), abs(t0 - e))
            key = (ov > 0, ov if ov > 0 else -gap, s)
            if (ov > 0 or gap <= 1.5) and (best is None or key > best[0]):
                best = (key, v)
        return best[1] if best else None

    per_word = [names.get(voice_at(w["start"])) for w in words]
    ends = lambda j: words[j - 1]["text"].rstrip("\"'”’)").endswith((".", "!", "?"))
    turn_start = lambda j: j == 0 or ends(j) or words[j]["start"] - words[j - 1]["start"] >= 1.0
    cuts = []
    for c in (i for i in range(1, len(words)) if per_word[i] != per_word[i - 1]):
        near = [j for j in range(max(1, c - 40), min(len(words), c + 40))
                if turn_start(j) and abs(words[j]["start"] - words[c]["start"]) <= NAMETAG_SNAP]
        cuts.append(min(near, key=lambda j: abs(words[j]["start"] - words[c]["start"])) if near else c)
    starts_i = [0] + sorted(set(cuts))
    out = []
    for a, b in zip(starts_i, starts_i[1:] + [len(words)]):
        who = max(Counter(per_word[a:b]).items(), key=lambda kv: kv[1])[0]
        if not out or out[-1][1] != who:
            out.append((words[a]["start"], who))
    return out


def segments_from_youtube(session, artists, vocab_terms, review_rows):
    path = TRANSCRIPTS_DIR / f"{slug(session)}.json"
    with open(path) as f:
        data = json.load(f)
    words = data.get("words") or []
    if not words:
        return []

    speaker_index = sorted((s for s in session.get("speakers", []) if s.get("start_seconds") is not None),
                           key=lambda s: s["start_seconds"])
    boundaries = [(s["start_seconds"], s["name"]) for s in speaker_index]
    nametag = nametag_boundaries(session, words, artists)
    voices = voice_boundaries(session, words)
    if voices:  # voices confirmed by a person (or by two agreeing sources they approved): best evidence
        boundaries = voices
    elif nametag:  # read off the video: finer and more reliable than the description's index
        boundaries = nametag

    segments = []
    if not boundaries:
        # No speaker index to slice by (Stage 1 flagged speaker_index_missing)
        # -- emit the transcript as unattributed time blocks rather than
        # silently dropping real transcript content from the corpus.
        segments.append({"speaker": None, "start": words[0]["start"], "boundary_end": None})
    elif boundaries[0][0] > words[0]["start"] + 5 and session.get("moderator"):
        segments.append({"speaker": session["moderator"], "start": words[0]["start"], "boundary_end": boundaries[0][0]})
    for i, (start, name) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else None
        segments.append({"speaker": name, "start": start, "boundary_end": end})

    known_terms = build_known_terms(session, vocab_terms, artists)
    result = []
    for seg in segments:
        seg_words = [w for w in words if w["start"] >= seg["start"] and (seg["boundary_end"] is None or w["start"] < seg["boundary_end"])]
        if not seg_words:
            continue
        # unattributed stretches (no speaker index, or a "Discussion" with many
        # voices) get cut into ~3-minute blocks so every hit deep-links to a
        # moment near where it was said
        group = seg["speaker"] is None or is_not_speaker(seg["speaker"])
        if group:
            cuts = time_block_starts(seg_words)
            chunks = [[w for w in seg_words if w["start"] >= a and (b is None or w["start"] < b)]
                      for a, b in zip(cuts, cuts[1:] + [None])]
        else:
            chunks, cuts = [seg_words], [seg["start"]]
        for k, chunk in enumerate(chunks):
            if not chunk:
                continue
            if session.get("transcript_source") in ("youtube-subtitles", "whisper-large-v3"):
                text, para_starts = punctuate_subtitles(chunk)
            else:
                text, para_starts = restore_punctuation(chunk)
            para_maps = word_time_maps(chunk, text)      # from the paragraph text as built, before corrections change it
            text, _ = correct_text(text, session, artists, vocab_terms, slug(session), chunk[0]["start"], review_rows, [])
            text = capitalize_known_terms(text, known_terms)
            start = seg["start"] if k == 0 else cuts[k]
            end = cuts[k + 1] if k + 1 < len(cuts) else seg["boundary_end"]
            result.append({"speaker": seg["speaker"], "start": start, "end": end, "text": text, "para_starts": para_starts,
                           "para_maps": para_maps})
    return result


def process_session(session, artists, vocab_terms, review_rows):
    source = session.get("transcript_source")
    if source == "zoom-transcript":
        segments = segments_from_zoom(session, artists, vocab_terms)
    elif source in ("youtube-auto-captions", "youtube-subtitles", "whisper-large-v3"):
        segments = segments_from_youtube(session, artists, vocab_terms, review_rows)
    else:
        print(f"{label(session)}: no usable transcript source ({source}), skipping corpus build")
        return None

    # canonical names are applied only to what gets published -- the raw
    # speaker labels above still drive segmentation and Zoom-label matching
    for seg in segments:
        seg["text"] = apply_style_rules(italicize_titles(fix_spoken_decades(seg["text"].replace("&nbsp;", " "))))
        if seg["speaker"]:
            seg["speaker"] = canonical_name(seg["speaker"])
    session = {**session, "speakers": finalize_speakers(session.get("speakers", []))}
    if any(s.get("start_seconds") is not None for s in session["speakers"]):
        # Stage 1 can leave this flag behind when the index came from the site page instead
        session["flags"] = [f for f in session.get("flags") or [] if f != "speaker_index_missing"]
    if session.get("moderator"):
        session["moderator"] = canonical_name(session["moderator"])

    for seg in segments:            # every paragraph needs its own start time (the page shows one per paragraph)
        paras = seg["text"].split("\n\n")
        starts = seg.get("para_starts") or []
        if len(paras) != len(starts):
            print(f"  WARNING {label(session)} at {seg['start']}: {len(paras)} paragraphs but {len(starts)} start times; using the turn start only")
            seg["para_starts"] = [seg["start"]] + [None] * (len(paras) - 1)
        else:
            seg["para_starts"] = [round(s, 2) for s in starts]
            if seg["start"] is not None:
                seg["para_starts"][0] = seg["start"]     # the first paragraph shares the turn's printed time
    segments = apply_text_edits(session, segments)
    for seg in segments:            # a start time for every sentence (the search results play from the sentence before the match)
        paras = seg["text"].split("\n\n")
        maps = seg.pop("para_maps", None)
        pieces = seg.pop("para_pieces", None)
        if pieces:
            maps = [cue_time_map(pc) for pc in pieces]
        starts = seg["para_starts"]
        seg["sentence_times"] = [
            sentence_times(p, maps[k] if maps and k < len(maps) else None, starts[k] if starts[k] is not None else (seg["start"] or 0))
            for k, p in enumerate(paras)]
    frontmatter = build_frontmatter(session, segments)
    body = build_body(session, segments)
    md = f"{frontmatter}\n\n{body}\n"

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CORPUS_DIR / f"{slug(session)}.md"
    out_path.write_text(md)
    return out_path, segments, session


def main():
    with open(SESSIONS_PATH) as f:
        sessions = json.load(f)
    with open(ARTISTS_PATH) as f:
        artists = json.load(f)
    with open(VOCAB_PATH) as f:
        vocab_terms = json.load(f)["terms"]

    flags = [a for a in sys.argv[1:] if a.startswith("--")]   # --no-review: don't append to the name-review queue
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    resolve_country = build_country_resolver(artists)
    review_rows = []
    corpus_json = []

    for session in sessions:
        if not selected(session, args):
            continue
        result = process_session(session, artists, vocab_terms, review_rows)
        if result:
            out_path, segments, session = result
            print(f"{label(session)}: {len(segments)} segments -> {out_path}")
            corpus_json.append({**{k: session[k] for k in [
                "type", "number", "session_title", "date_recorded", "date_published",
                "video_id", "url", "duration_seconds", "moderator", "transcript_source", "flags",
            ]},
                **{k: session[k] for k in ("interviewee", "interviewer") if session.get(k)},
                "speakers": [
                    {
                        "name": s["name"],
                        "location": s.get("country"),
                        "country": resolve_country(s["name"], s.get("country")),
                        "start": s.get("start_seconds"),
                    }
                    for s in sorted(session.get("speakers", []), key=lambda s: s.get("start_seconds") or 0)
                ],
                "languages": ["en"],
                "segments": segments,
            })

    if not args:
        unmatched = [k for k, n in TITLE_HITS.items() if n == 0]
        print(f"Book titles italicized: {sum(TITLE_HITS.values())} across {len(TITLE_HITS) - len(unmatched)}/{len(TITLE_HITS)} entries")
        for k in unmatched:
            print(f"  WARNING: titles.json entry matched nothing: {k!r}")

    corpus_json_path = CORPUS_DIR / "corpus.json"
    existing = []
    if corpus_json_path.exists():
        with open(corpus_json_path) as f:
            existing = json.load(f)
    by_slug = {slug(e): e for e in existing}
    for entry in corpus_json:
        by_slug[slug(entry)] = entry
    type_order = list(TYPES)
    with open(corpus_json_path, "w") as f:
        json.dump(sorted(by_slug.values(), key=lambda e: (type_order.index(e.get("type", "salon")), e["number"])),
                  f, indent=2, ensure_ascii=False)
    print(f"\nWrote {corpus_json_path}")
    if not args:      # a full rebuild: remind about the most common transcription error ("Techspressionism" heard as "text-...")
        import subprocess
        out = subprocess.run([sys.executable, str(Path(__file__).with_name("check-techspressionism.py"))], capture_output=True, text=True).stdout
        print(out.splitlines()[0] if out else "Techspressionism check did not run")

    if review_rows and "--no-review" not in flags:
        import csv
        REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
        file_exists = REVIEW_CSV.exists()
        with open(REVIEW_CSV, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["session", "candidate_name", "matched_text", "similarity", "context", "cue_start"])
            if not file_exists:
                writer.writeheader()
            for row in review_rows:
                writer.writerow(row)
        print(f"{len(review_rows)} additional candidates written to {REVIEW_CSV}")


if __name__ == "__main__":
    main()
