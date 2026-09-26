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
STRONG_PRIOR_REVIEW = 0.63
INDEX_AUTO = 0.92
INDEX_REVIEW = 0.80

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

# function words that show up as the trailing/leading token of a fuzzy name
# window ("michael price is", "carter hi", "and spalter") -- eating them
# during a substitution corrupts the sentence
EDGE_STOPWORDS = {
    "a", "an", "the", "and", "or", "is", "was", "are", "were", "be", "been",
    "as", "at", "to", "of", "in", "on", "i", "i'm", "im", "you", "we", "he",
    "she", "it", "hi", "so", "but", "that", "this", "here", "there", "who",
    "with", "for", "my", "his", "her", "our", "oh", "now", "just", "would",
    "will", "can", "could", "has", "have", "had", "do", "does", "did", "not",
    "am", "by", "from", "up", "out", "if", "then", "than", "about", "us",
}


def apply_vocabulary(text, vocab_terms):
    substitutions = 0
    for term in vocab_terms:
        canonical = term["canonical"]
        for variant in term["known_asr_variants"]:
            body = re.escape(variant)
            if term.get("whole_word"):
                # opt-in: a variant that is a prefix of the correct form
                # ("jasper john" vs "Jasper Johns") must not match inside it
                body = rf"(?<!\w){body}(?!\w)"
            pattern = re.compile(body, re.IGNORECASE)
            text, n = pattern.subn(lambda m, c=canonical: c, text)
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


def correct_names_in_text(text, candidates, auto_threshold, review_threshold, session_number, cue_start, review_rows, corrections, single_token_auto_ok=True, known_good=frozenset()):
    tokens = tokenize_with_spans(text)
    # process longest names first so multi-word corrections don't get
    # clobbered by a shorter name matching a sub-span of an already-applied one
    name_token_count = {n: len(n.split()) for n in candidates}
    for name in sorted(candidates, key=lambda n: -name_token_count[n]):
        ratio, start, end = best_window_match(tokens, name)
        is_single_token = name_token_count[name] == 1
        if start is not None and not is_single_token:
            # if the window's edge tokens are function words that don't
            # correspond to a name token ("michael price IS", "AND spalter",
            # "carter HI"), trim them out of the span and re-score -- eating
            # them during a substitution corrupts the sentence
            span_words = text[start:end].split()
            nlow = [w.lower() for w in name.split()]

            def edge_junk(w, name_word):
                w = w.strip(".,!?;:'").lower()
                return w in EDGE_STOPWORDS and w != name_word and (not name_word or w[0] != name_word[0])

            while len(span_words) > 1 and edge_junk(span_words[-1], nlow[-1]):
                end -= len(span_words[-1]) + 1
                span_words.pop()
            while len(span_words) > 1 and edge_junk(span_words[0], nlow[0]):
                start += len(span_words[0]) + 1
                span_words.pop(0)
            ratio = SequenceMatcher(None, " ".join(span_words).lower().strip(".,!?;:"), name.lower()).ratio()
            # a good multi-word name match needs the *last* tokens to actually
            # correspond -- otherwise "Michael worked" scores 0.73 against
            # "Michael Woodruff" on the first token alone and eats "worked"
            if len(span_words) >= 2:
                last_sim = SequenceMatcher(None, span_words[-1].strip(".,!?;:'").lower(), nlow[-1]).ratio()
                if last_sim < 0.45:
                    ratio = min(ratio, review_threshold - 0.01)
        if ratio >= auto_threshold and (single_token_auto_ok or not is_single_token):
            original = text[start:end]
            # a matched span that's itself a recognized, correctly-spelled word/phrase (an artist index entry or
            # vocab canonical) is never auto-corrected away, even at a high ratio -- otherwise an ordinary phrase
            # that happens to share a name's ending word ("be an artist" vs. Salon 109's own "Beau Tardy Artist")
            # gets silently rewritten into that name. Found and fixed 2026-09-22 after Colin's own correction of
            # that exact line kept reverting on rebuild.
            if original.lower() != name.lower() and original.strip(".,!?;:").lower() not in known_good:
                text = text[:start] + name + text[end:]
                corrections.append({"session": session_number, "original": original, "corrected": name, "ratio": round(ratio, 2), "cue_start": cue_start})
                tokens = tokenize_with_spans(text)
        elif ratio >= review_threshold:
            original = text[start:end]
            if (original.lower(), name) in SUPPRESSED_PAIRS:
                continue
            # the matched span is already a correctly-spelled name (an artist
            # index entry or vocab canonical) -- nothing to review, it's just
            # a near-collision with a *different* real person
            if original.strip(".,!?;:").lower() in known_good:
                continue
            # short single-token weak-prior candidates (cryptic handles like
            # "MCHX", "cha", "SUDO", "LORDOF") fuzzy-match hundreds of ordinary
            # words and can never auto-correct anyway -- only surface a
            # near-exact hit worth a human glance, not the long tail of noise
            if not single_token_auto_ok and is_single_token and len(name) <= 6 and ratio < 0.95:
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
    known_good = {a["name"].lower() for a in artists} | {t["canonical"].lower() for t in vocab_terms}
    text = correct_names_in_text(text, strong_prior, STRONG_PRIOR_AUTO, STRONG_PRIOR_REVIEW, session_number, cue_start, review_rows, corrections, single_token_auto_ok=True, known_good=known_good)
    text = correct_names_in_text(text, index_names, INDEX_AUTO, INDEX_REVIEW, session_number, cue_start, review_rows, corrections, single_token_auto_ok=False, known_good=known_good)
    return text, n


# ---------------------------------------------------------------------------------------------------------------
# House style rules that apply to EVERY transcript, now and in future (Colin, 20 September 2026: "a rule forever").
#   1. Any misspelling of "Techspressionism" / "Techspressionist" (Techressionist, Techspressism, Techpressionist ...)
#      is corrected to the right word. A word is treated as a misspelling when it starts with "Tech"/"Tex"/"Tec",
#      ends in -ism / -ist (or their plurals), and is at least 85% similar to the correct word.
#   2. "Salon" is capitalized when it follows "Techspressionist" ("Techspressionist Salon").
#   3. "Number" is capitalized when it follows "Techspressionist Salon" ("Techspressionist Salon Number 81").
# Plural "salons" and other uses of "salon" are left alone.
# ---------------------------------------------------------------------------------------------------------------
from difflib import SequenceMatcher as _SM

_TECH_TARGET = {"ism": "Techspressionism", "ist": "Techspressionist"}
_TECH_WORD = re.compile(r"\b[Tt][A-Za-z-]{7,21}(?:ism|ist)s?\b")

# Colin, 22 September 2026: "Ai"/"ai" should always be the two-letter acronym "AI" -- Zoom
# capitalizes only the first letter of a caption line ("Ai"), and Whisper/YouTube auto-captions
# often leave it bare lowercase ("ai"). Excludes the artist Ai Weiwei, whose name is not the
# acronym. "OpenAI" and "ChatGPT" are heard as two separate words with stray capitalization or
# punctuation between them and are always one word, matching the correct product names.
_OPENAI_RE = re.compile(r"\bopen\s+ai\b", re.IGNORECASE)
_CHATGPT_RE = re.compile(r"\bchat[\s,]+gpt\b", re.IGNORECASE)
_GPT_NUM_RE = re.compile(r"\bgpt-?\s?(two|three|four|five|\d+)\b", re.IGNORECASE)
_GPT_BARE_RE = re.compile(r"\bgpt\b", re.IGNORECASE)
_AI_RE = re.compile(r"\b[Aa][Ii]\b(?!\s+[Ww]eiwei)")


def fix_ai_terms(text):
    text = _OPENAI_RE.sub("OpenAI", text)
    text = _CHATGPT_RE.sub("ChatGPT", text)
    text = _GPT_NUM_RE.sub(lambda m: "GPT-" + m.group(1).lower(), text)
    text = _GPT_BARE_RE.sub("GPT", text)
    text = _AI_RE.sub("AI", text)
    return text


def fix_techspressionism(text):
    def repl(m):
        word = m.group(0)
        core = word.lower()
        plural = core.endswith(("ists", "isms"))
        stem = core[:-1] if plural else core
        canon = _TECH_TARGET.get(stem[-3:])
        if not canon or not stem.startswith(("tech", "tex", "tec")):
            return word
        if stem == canon.lower() or _SM(None, stem.replace("-", ""), canon.lower()).ratio() >= 0.85:
            return canon + ("s" if plural else "")
        return word
    return _TECH_WORD.sub(repl, text)


_EMAIL = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9._%+-]*)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)\.([A-Za-z]{2,})\b")


def spell_out_emails(text):
    """Speech recognition turns "name at company dot com" into something that looks like an email address. The archive
    never prints one (privacy): it is written out in words, as it was spoken, so no address can be harvested from it."""
    return _EMAIL.sub(lambda m: f"{m.group(1)} at {m.group(2).lower()} dot {m.group(3).lower()}", text)


# ---- fillers and stutters: "clean verbatim" (project_archive_transcript_style: uh/um out, like/you know stay) ----
# Colin, 2026-09-22: make every transcript read naturally aloud, with no grammatical/capitalization/spelling
# errors, without touching name handling. These two passes only ever remove/collapse tokens from a small,
# hand-verified set (never a name, never an unrecognized word) -- anything outside that set is left alone for
# human review (see scripts/quality-report.py's filler/stutter section) rather than guessed at automatically.

_FILLER = r"(?<!-)(?:um|uh)(?!-)"   # never "uh-huh"/"uh-uh"/"um-hmm" -- real words, not disfluencies


def _cap_first(s):
    for i, ch in enumerate(s):
        if ch.isalpha():
            return s[:i] + ch.upper() + s[i + 1:]
        if not ch.isspace():
            break
    return s


def remove_fillers(text):
    """Strip standalone "uh"/"um" disfluencies. Cleans up the punctuation/spacing left behind and
    re-capitalizes the next word when the filler removed was the start of a sentence."""
    def fix_par(par):
        def sent_repl(m):
            return m.group("lead") + _cap_first(m.group("rest"))
        par = re.sub(rf"(?P<lead>^|[.!?\u2026]\s+)\b{_FILLER}\b[,.]?\s+(?P<rest>\S)", sent_repl, par, flags=re.IGNORECASE)
        par = re.sub(rf",\s*\b{_FILLER}\b\s*,", ",", par, flags=re.IGNORECASE)          # ", um," mid-sentence
        par = re.sub(rf",\s*\b{_FILLER}\b(?=\s)", ",", par, flags=re.IGNORECASE)        # ", um" (no closing comma)
        par = re.sub(rf"\b{_FILLER}\b\s*,\s*", ", ", par, flags=re.IGNORECASE)          # "um, " (no leading comma)
        par = re.sub(rf"\s*\b{_FILLER}\b\s*", " ", par, flags=re.IGNORECASE)            # whatever's left, bare
        par = re.sub(r"[ \t]{2,}", " ", par)
        par = re.sub(r"[ \t]+([.!?,;:])", r"\1", par)
        return par.strip()
    return "\n\n".join(fix_par(p) for p in text.split("\n\n"))


# common function/filler words and contractions hand-verified (2026-09-22, ~750 real instances read in
# context across the corpus) to be a stutter every time they repeat back to back in this transcript set --
# e.g. "and and", "is is", "so so", "really really", "yeah yeah yeah", "it's it's", "how how". A word NOT in
# this set is never touched by collapse_stutters(), even if repeated -- that includes anything that could be
# a name ("Ann Ann") or a real repeated phrase (protected explicitly below): flagged for a human to look at
# instead (review/repeated-word-candidates.csv), never guessed at.
SAFE_STUTTER_WORDS = frozenset("""
and the that is a to in of it this you my for so with but on if as they your when just these some he
are from there we or very really yeah no like what an be was were will would can could has have had
do does did not who which at by i know think going want kind she him them its our us out about all been get got
other right any his because then okay how where their those one different maybe yes more back
it's that's i'm there's we're they're you're i'll i've he's she's who's let's what's here's
won't don't didn't doesn't isn't aren't wasn't weren't can't couldn't wouldn't shouldn't
haven't hasn't hadn't that'll you'll we'll they'll i'd you'd he'd she'd we'd they'd
""".split())

_STUTTER_PROTECTED_PAIRS = {("wild", "wild"), ("bye", "bye")}   # "the wild wild west", "bye bye" -- idioms, not disfluencies


EMPHASIS_WORDS = frozenset("very really so no yes more right okay".split())    # repeats that are usually meant ("very very", "no no"); the final pass in Stage 5 leaves them alone (Colin, 26 Sep 2026)


def collapse_stutters(text, keep=frozenset()):
    """Collapse an immediately-repeated word ("and and", "so so so") to one occurrence, but only for a word in
    SAFE_STUTTER_WORDS -- never a name or any other word outside that hand-verified list."""
    def repl(m):
        first = m.group(1)
        low = first.lower()
        if low not in SAFE_STUTTER_WORDS or low in keep:
            return m.group(0)
        rest = re.findall(r"[A-Za-z']+", m.group(0))[1:]
        if any((low, w.lower()) in _STUTTER_PROTECTED_PAIRS for w in rest):
            return m.group(0)
        return first
    return re.sub(r"\b([A-Za-z']+)\b(?:[ \t]+\1\b)+", repl, text, flags=re.IGNORECASE)


_NON_LATIN_SCRIPT = re.compile(
    "["
    "\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f"      # Hangul (syllables, jamo, compatibility jamo)
    "\u4e00-\u9fff\u3400-\u4dbf"                    # CJK Unified Ideographs + Extension A
    "\u3040-\u30ff"                                  # Hiragana + Katakana
    "\u0400-\u04ff"                                  # Cyrillic
    "\u0600-\u06ff\u0750-\u077f"                     # Arabic + Arabic Supplement
    "\u0590-\u05ff"                                  # Hebrew
    "\u0e00-\u0e7f"                                  # Thai
    "]+"
)


def remove_non_latin_hallucinations(text):
    """Strip stray runs of non-Latin script (Hangul, CJK, Cyrillic, Arabic, Hebrew, Thai) inserted mid-sentence --
    a well-documented Whisper failure mode (see review/whisper-hallucinations.csv: 122 instances, 100% confined
    to whisper-large-v3-sourced sessions, often the same fabricated fragment recurring across unrelated
    recordings, e.g. "\uac04\ub2e8ity"/"\uac04\ub2e8ities" -- the model fabricating plausible-looking text during silence or
    unclear audio, not a mistranscription of anything actually said). Per Colin, 2026-09-22: the archive is
    English-only, strip on sight. Most cases are a whole inserted "word"; a few show a single foreign character
    substituted for what should have been a Latin letter mid-word ("\u0441abinet") -- there's no reliable way to
    know algorithmically what letter belonged there, so those are left slightly short a letter rather than guessed at."""
    def fix_par(par):
        par = _NON_LATIN_SCRIPT.sub("", par)
        par = re.sub(r"[ \t]{2,}", " ", par)
        par = re.sub(r"[ \t]+([.!?,;:])", r"\1", par)
        return par.strip()
    return "\n\n".join(fix_par(p) for p in text.split("\n\n"))


# ---- mechanical clean-ups (Colin, 25 Sep 2026: "fix" the lower-case product names, a/an mistakes and doubled punctuation) --------------------------------
_PRODUCT_NAMES = {"ipad": "iPad", "ipads": "iPads", "iphone": "iPhone", "iphones": "iPhones", "ipod": "iPod", "imovie": "iMovie", "ios": "iOS", "itunes": "iTunes",
                  "macbook": "MacBook", "wordpress": "WordPress", "instagram": "Instagram", "youtube": "YouTube", "photoshop": "Photoshop", "pokemon": "Pok\u00e9mon",
                  "icolorama": "iColorama"}
# not inside an address or handle (instagram.com/x, @youtube, a-b-photoshop)
_PRODUCT_RE = re.compile(r"(?<![\w./@\-])(" + "|".join(_PRODUCT_NAMES) + r")(?![\w@\-]|\.[a-z]|/)", re.IGNORECASE)


def fix_product_names(text):
    return _PRODUCT_RE.sub(lambda m: _PRODUCT_NAMES[m.group(1).lower()], text)


_FALSE_START = r"in|into|and|as|at|on|if|or|of|it|its|it's|is|i|i'm|i've|i'll|i'd|are|all|also|about|after|again|ago|any|another|around|always|actually|even|either|every|everything|ever|else|each|enough|often|only|over|out|off|up|upon|under|already|almost|although|among|anyway|anyone|anything|are|aren't|as|a|an"
_A_BEFORE_VOWEL = re.compile(r"\b([Aa])( +)(?!(?i:" + _FALSE_START + r")\b)(?=(?i:[aeio][a-z]|hour|honest|honor|honour|heir)[A-Za-z\-']*\b)(?!(?i:one\b|once\b|ones\b|eu|ewe\b|oui\b))")
_ACRONYMS = "nft|nfts|mfa|fbi|sql|svg|html|xml|mri|lcd|led|rss|sms|mba|mvp|ftp|faq|fyi|hdr|nda|npc|nyc|mp|rf|lsd|std|sos|ssd|xr|nsa|mit|mfa|rn|hr|fx|rgb|lcd|smtp|mfc|fcc|nba|nhl|nfl|mtv|npr|nyu|sva"
_AN_BEFORE_CONSONANT = re.compile(r"\b([Aa]n)( +)(?!(?i:" + _ACRONYMS + r")\b)(?=[bcdfgjklmnpqrstvwxz](?=[a-z]*[aeiouy])[a-z]{2,}\b)")


def fix_articles(text):
    """"a artist" -> "an artist", "an painting" -> "a painting". Only clear cases: a before a word that starts with a, e, i or o (not "one", "once", "eu..."),
    or the silent-h words hour, honest, honor, heir; "an" before a lower-case word that starts with a consonant (not h, y, or a spelled-out letter). Words starting
    with u are left alone (a unique / an umbrella need the sound, not the letter)."""
    text = _A_BEFORE_VOWEL.sub(lambda m: m.group(1) + "n" + m.group(2), text)
    return _AN_BEFORE_CONSONANT.sub(lambda m: m.group(1)[:-1] + m.group(2), text)


_ARTIST_NAME_FIXES = [      # (pattern, correction): a misheard or misspelled name, only in the phrase where the surrounding words show who is meant (Colin, 25 Sep 2026)
    (r"\bPollick[- ]Krasner\b|\bPollock Brasner\b", "Pollock-Krasner"),
    (r"\bLee Kasner\b", "Lee Krasner"),
    (r"\bKanditsky\b", "Kandinsky"),
    (r"\bStena Vasuka\b|\bStane of Vesulka\b", "Steina Vasulka"),
    (r"\bRoman Virosko\b", "Roman Verostko"),
    (r"\bVera (?:Monar|Mohner)\b", "Vera Molnar"),
    (r"\bMaria Abramovich\b", "Marina Abramovi\u0107"),
    (r"\bEgon Schiegel\b|\bcalled Schiegel\b", lambda m: "Egon Schiele" if m.group(0).startswith("Egon") else "called Schiele"),
    (r"\bRinehart(?=, Motherwell)", "Reinhardt"),
    (r"\bRuth Levitt(?=, Artist and Computer)", "Ruth Leavitt"),
    (r"\bHans Hoffman\b", "Hans Hofmann"),
    (r"\bFran(?:z|ce) Klein\b", "Franz Kline"),
    (r"\bManfred Moore\b", "Manfred Mohr"),
    (r"\bBen Laposki\b", "Ben Laposky"),
    (r"\bchuck, Suri\b", "Chuck Csuri"),
    (r"\bHarold Cohn\b", "Harold Cohen"),
    (r"\bNam June Pike\b", "Nam June Paik"),
]
_ARTIST_NAME_FIXES += [        # confirmed by hand from review/artist-name-candidates.csv (25 Sep 2026)
    (r"\bFritz Feiss\b", "Fritz Faiss"),
    (r"\bDavid Salley\b", "David Salle"),
    (r"\bDonna Haraways work\b", "Donna Haraway's work"),
    (r"\bMary Heilman\b", "Mary Heilmann"),
    (r"\bVisc[oa]m\b", "Vizcom"),        # the AI design tool (Salon 88: jewelry design, Leonardo.AI, Bing Image Creator)
]
try:      # written by scripts/scan-notable-artists.py: misspelled first+last names of notable artists (Wikipedia's artist categories), fixed where the words around them are about art
    import json as _json
    from pathlib import Path as _Path
    for _f in _json.loads((_Path(__file__).resolve().parent.parent / "data" / "artist-name-fixes.json").read_text()):
        _ARTIST_NAME_FIXES.append((_f["pattern"], _f["replacement"]))
except (OSError, ValueError):
    pass
_ARTIST_NAME_RES = [(re.compile(p, re.IGNORECASE), r) for p, r in _ARTIST_NAME_FIXES]


def fix_artist_names(text):
    for rx, r in _ARTIST_NAME_RES:
        text = rx.sub(r, text)
    return text


def fix_doubled_punctuation(text):
    text = re.sub(r"(?<![.\u2026])\.\.(?![.\u2026])", ".", text)      # ".." -> "." ("..." is an ellipsis and stays)
    return re.sub(r",{2,}", ",", text)


def apply_style_rules(text):
    text = fix_artist_names(fix_doubled_punctuation(fix_articles(fix_product_names(fix_ai_terms(spell_out_emails(fix_techspressionism(text)))))))
    text = re.sub(r"(?:(?<=\s)|^)\.(?:\s+\.){2,}(?=\s|$)", "\u2026", text)          # a run of stray periods (Whisper in silence) becomes one ellipsis
    text = re.sub(r"\b(Techspressionist) salon\b", r"\1 Salon", text)
    text = re.sub(r"\b(Techspressionist Salon) number\b", r"\1 Number", text)
    text = remove_non_latin_hallucinations(text)
    text = collapse_stutters(remove_fillers(text))
    return text


# words that are capitalised on purpose in running text and must never be lowered by the mid-sentence fix (names, months, days, brands, nationalities ...)
KEEP_CAPITAL = set("""i january february march april may june july august september october november december monday tuesday wednesday thursday friday
saturday sunday zoom instagram google youtube facebook twitter tiktok discord clubhouse chatgpt openai adobe photoshop illustrator apple ipad iphone mac windows
english french german spanish italian russian chinese japanese korean iranian american canadian british european african asian indian brazilian australian
christmas easter god internet web nft nfts ai salon number museum gallery center university college institute art arts loop""".split())


# ---- capitals in the middle of a sentence -----------------------------------------------------------------------------------------------
# Zoom starts every caption line with a capital, even when the sentence goes on ("...founders of Loop, and Have her talk"). A capitalised word is
# lowered only when ALL of these hold: it is an ordinary word (data/lowercase-words.json: common in lower case, never a name), it comes straight after
# a word that cannot end a sentence (and, of, the, is, ...), and no other capitalised word stands close by in the same sentence (that would be a
# name or a title: "Institute of Technology", "Call for Artists"). Names, "I" and the first word of a sentence are never touched.
CONTINUERS = set("""and but or the a an of to in on at for with from by as if because than my your his her our their is are was were be been am has have
had will would can could should might may into onto about over under between through during without within""".split())
_ORDINARY = []
_SENT_END = re.compile(r"[.?!\u2026:\u201d\"]['\u2019)\]]*$")
_CORE = re.compile(r"^[\"'\u201c\u2018(\[_]*([A-Za-z][A-Za-z'\u2019-]*)[\"'\u201d\u2019)\].,;:!?_\u2026-]*$")


def _ordinary_words():
    if not _ORDINARY:
        import json as _json
        from pathlib import Path as _Path
        path = _Path(__file__).resolve().parent.parent / "data" / "lowercase-words.json"
        _ORDINARY.append(set(_json.loads(path.read_text())["words"]) if path.exists() else set())
    return _ORDINARY[0]


def midsentence_caps(text):
    """[(index of the capital letter, the word)] that should be lower case."""
    ordinary = _ordinary_words()
    toks = [(m.group(), m.start()) for m in re.finditer(r"\S+", text)]
    starts_sentence = lambda j: j == 0 or bool(_SENT_END.search(toks[j - 1][0]))

    def capital(j):
        m = _CORE.match(toks[j][0])
        return bool(m) and m.group(1)[0].isupper() and m.group(1) != "I" and not starts_sentence(j)
    out = []
    for i in range(1, len(toks)):
        m = _CORE.match(toks[i][0])
        if not m or _SENT_END.search(toks[i - 1][0]):
            continue
        w = m.group(1)
        prev = _CORE.match(toks[i - 1][0])
        prev_w = prev.group(1) if prev else ""
        if not (w[0].isupper() and w[1:].islower()) or w in ("I",) or len(w) < 2:
            continue
        if prev_w not in CONTINUERS or re.search(r"[.?!\u2026]$", toks[i - 1][0]) or w.lower() not in ordinary or w.lower() in KEEP_CAPITAL:
            continue
        near = [j for j in range(max(0, i - 3), min(len(toks), i + 4)) if j != i]
        # stop at sentence ends: only tokens of the same sentence count
        back = []
        for j in range(i - 1, max(-1, i - 4), -1):
            back.append(j)
            if starts_sentence(j):
                break
        fwd = []
        for j in range(i + 1, min(len(toks), i + 4)):
            if _SENT_END.search(toks[j - 1][0]):
                break
            fwd.append(j)
        if any(capital(j) for j in back + fwd):
            continue
        out.append((toks[i][1] + toks[i][0].index(w[0]), w))
    return out


def fix_midsentence_caps(text):
    parts = text.split("\n\n")
    fixed = []
    for par in parts:
        chars = list(par)
        for idx, _w in midsentence_caps(par):
            chars[idx] = chars[idx].lower()
        fixed.append("".join(chars))
    return "\n\n".join(fixed)
