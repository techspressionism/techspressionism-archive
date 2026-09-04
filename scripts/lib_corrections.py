#!/usr/bin/env python3
"""Shared vocabulary/name-correction logic, used by both Stage 4
(04-correct-names.py, whole-transcript correction + review-queue
generation) and Stage 5 (05-build-corpus.py, per-segment correction after
slicing a flat YouTube-sourced transcript by speaker)."""
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

STRONG_PRIOR_AUTO = 0.72
STRONG_PRIOR_REVIEW = 0.55
INDEX_AUTO = 0.92
INDEX_REVIEW = 0.75

SUPPRESSIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "review-suppressions.json"


def load_suppressions():
    """Pairs of (matched_text, candidate_name), lowercased, known from prior
    review passes to be coincidental word-collisions rather than genuine
    name mentions -- e.g. "mean" fuzzy-matching the artist handle "meta".
    Curated by hand, not by raw frequency: a pair that recurs often can
    just as easily mean "this real correction is needed often" (e.g. "davo
    bradley" -> "Davonte Bradley", a known nickname) as "this is noise" --
    see data/review-suppressions.json for the reasoning behind what's here
    and what was deliberately excluded despite high counts."""
    if not SUPPRESSIONS_PATH.exists():
        return set()
    with open(SUPPRESSIONS_PATH) as f:
        return {(x["matched_text"].lower(), x["candidate_name"]) for x in json.load(f)}


SUPPRESSED_PAIRS = load_suppressions()

WORD_RE = re.compile(r"\S+")
SENTENCE_END_RE = re.compile(r"[.!?]\s*$")
EDGE_PUNCT_RE = re.compile(r"^[^\w]+|[^\w]+$")


def apply_vocabulary(text, vocab_terms):
    substitutions = 0
    for term in vocab_terms:
        canonical = term["canonical"]
        for variant in term["known_asr_variants"]:
            pattern = re.compile(re.escape(variant), re.IGNORECASE)
            text, n = pattern.subn(canonical, text)
            substitutions += n
    return text, substitutions


def tokenize_with_spans(text):
    return [(m.group(), m.start(), m.end()) for m in WORD_RE.finditer(text)]


def best_window_match(tokens, name):
    """Slide a window the length of `name`'s tokens across `tokens`,
    return (ratio, char_start, char_end) for the best match. The span is
    trimmed to exclude leading/trailing punctuation so a substitution
    doesn't eat a comma or period attached to the matched word(s), and
    windows that cross a sentence boundary (a non-final token ending in
    . ! ?) are rejected -- a name can't legitimately span two sentences."""
    name_tokens = name.split()
    n = len(name_tokens)
    if n == 0 or len(tokens) < n:
        return 0.0, None, None
    first_letter = name_tokens[0][0].lower()
    best = (0.0, None, None)
    for i in range(len(tokens) - n + 1):
        if tokens[i][0][:1].lower() != first_letter:
            continue
        if n > 1 and any(SENTENCE_END_RE.search(tokens[i + k][0]) for k in range(n - 1)):
            continue
        window_words = [tokens[i + k][0] for k in range(n)]
        window_text = " ".join(window_words)
        stripped = EDGE_PUNCT_RE.sub("", window_text)
        ratio = SequenceMatcher(None, stripped.lower(), name.lower()).ratio()
        if ratio > best[0]:
            raw_start, raw_end = tokens[i][1], tokens[i + n - 1][2]
            trail_match = re.search(r"[^\w]+$", window_text)
            trail_trim = len(trail_match.group()) if trail_match else 0
            lead_match = re.search(r"^[^\w]+", window_text)
            lead_trim = len(lead_match.group()) if lead_match else 0
            best = (ratio, raw_start + lead_trim, raw_end - trail_trim)
    return best


def correct_names_in_text(text, candidates, auto_threshold, review_threshold, session_number, cue_start, review_rows, corrections, single_token_auto_ok=True):
    tokens = tokenize_with_spans(text)
    # process longest names first so multi-word corrections don't get
    # clobbered by a shorter name matching a sub-span of an already-applied one
    for name in sorted(candidates, key=lambda n: -len(n.split())):
        ratio, start, end = best_window_match(tokens, name)
        is_single_token = len(name.split()) == 1
        if ratio >= auto_threshold and (single_token_auto_ok or not is_single_token):
            original = text[start:end]
            if original.lower() != name.lower():
                text = text[:start] + name + text[end:]
                corrections.append({"session": session_number, "original": original, "corrected": name, "ratio": round(ratio, 2), "cue_start": cue_start})
                tokens = tokenize_with_spans(text)
        elif ratio >= review_threshold:
            original = text[start:end]
            if (original.lower(), name) in SUPPRESSED_PAIRS:
                continue
            context_start = max(0, start - 60)
            context_end = min(len(text), end + 60)
            review_rows.append({
                "session": session_number, "candidate_name": name, "matched_text": original,
                "similarity": round(ratio, 2), "context": text[context_start:context_end].replace("\n", " "),
                "cue_start": cue_start,
            })
    return text


def vocab_canonicals(vocab_terms):
    return {t["canonical"].lower() for t in vocab_terms}


def build_candidate_pools(session, artists, vocab_terms):
    """Names that collide with a controlled-vocabulary term (e.g. two artist
    index entries are literally named "Techspressionism" / "Abstract
    Techspressionism" -- a data-entry issue on the site, not a person's
    name) must never enter the name-candidate pool: vocabulary handling
    already owns corrections *toward* that term, and letting it double as a
    "person name" risks corrupting unrelated real words/phrases that
    happen to resemble it (this nearly rewrote "Abstract Expressionism")."""
    canonicals = vocab_canonicals(vocab_terms)
    strong_prior = [s["name"] for s in session.get("speakers", []) if s["name"].lower() not in canonicals]
    index_names = [a["name"] for a in artists if a["name"] not in strong_prior and a["name"].lower() not in canonicals]
    return strong_prior, index_names


def correct_text(text, session, artists, vocab_terms, session_number, cue_start, review_rows, corrections):
    """Full correction pass (vocabulary + both name pools) on one span of text."""
    text, n = apply_vocabulary(text, vocab_terms)
    strong_prior, index_names = build_candidate_pools(session, artists, vocab_terms)
    text = correct_names_in_text(text, strong_prior, STRONG_PRIOR_AUTO, STRONG_PRIOR_REVIEW, session_number, cue_start, review_rows, corrections, single_token_auto_ok=True)
    text = correct_names_in_text(text, index_names, INDEX_AUTO, INDEX_REVIEW, session_number, cue_start, review_rows, corrections, single_token_auto_ok=False)
    return text, n
