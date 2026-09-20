"""Sentence splitting and sentence start times, shared by Stage 5 (which computes them) and Stage 6 (which
publishes them for the search results).

A sentence ends at a whitespace-separated word that ends with . ? ! or an ellipsis (optionally followed by a closing
quote or bracket), unless the word is a common abbreviation ("Dr.", "Mr.", ...). The search page's script uses the very
same rule on the page text, so "sentence number k of this paragraph" means the same thing in both places.

A paragraph carries a TIME MAP: pairs [fraction of the paragraph's text, seconds]. For speech-recognition text the pairs
are the words (exact); for Zoom text they are the cue boundaries (the position inside a cue is interpolated, so a
sentence start is good to about a second). The time of a sentence is read off the map at the sentence's position.
"""
import re

SENTENCE_END = re.compile(r"[.?!…][\"'”’)\]]*$")
ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "etc.", "e.g.", "i.e.", "no.", "jr.", "sr.", "prof."}


def ends_sentence(word):
    return bool(SENTENCE_END.search(word)) and word.lower() not in ABBREVIATIONS


def split_sentences(text):
    """[(start_char, end_char, sentence_text)] for one paragraph."""
    words = list(re.finditer(r"\S+", text))
    out, start = [], None
    for i, m in enumerate(words):
        if start is None:
            start = m.start()
        if ends_sentence(m.group()) or i == len(words) - 1:
            out.append((start, m.end(), " ".join(text[start:m.end()].split())))
            start = None
    return out


def time_at(tmap, fraction):
    """Seconds at `fraction` (0..1) of a paragraph, from its time map [[fraction, seconds], ...] (ascending)."""
    if not tmap:
        return None
    if fraction <= tmap[0][0]:
        return tmap[0][1]
    for (f0, t0), (f1, t1) in zip(tmap, tmap[1:]):
        if fraction <= f1:
            if f1 <= f0:
                return t1
            return t0 + (t1 - t0) * (fraction - f0) / (f1 - f0)
    return tmap[-1][1]


def sentence_times(paragraph_text, tmap, paragraph_start):
    """One start time per sentence of the paragraph. The first sentence starts where the paragraph does."""
    sents = split_sentences(paragraph_text)
    n = max(1, len(paragraph_text))
    times = []
    for k, (s0, _e, _t) in enumerate(sents):
        t = paragraph_start if k == 0 else time_at(tmap, s0 / n)
        if t is None:
            t = paragraph_start
        if times and t < times[-1]:
            t = times[-1]
        times.append(round(float(t), 2))
    return times
