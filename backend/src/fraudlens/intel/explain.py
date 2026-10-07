"""Which words in the customer's own message made the classifier say so.

Two kinds of evidence, both pointing at spans of the text the customer pasted, in
whatever script it was written:

- **phrases**: for each cue that matched (`cues.yaml`), the exact stretch of text
  the pattern matched ("OTP টা বলুন", "pin ta bolen", "registration fee").
- **highlights**: the words that pushed the scam score up most. The character
  n-grams the model reads never cross a word boundary (`char_wb`), so each n-gram's
  weight belongs to exactly one word; a word's share is the sum of its n-grams'.

Nothing is generated: both are read off the patterns and the model's weights.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from sklearn.feature_extraction.text import TfidfVectorizer

from .cues import Cue, matched
from .links import URL

MAX_CHARS = 2000
MAX_PHRASES = 3  # per cue
MAX_HIGHLIGHTS = 5

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_DIGIT = re.compile(r"\d")


def mapped(text: str) -> tuple[str, list[int], list[int]]:
    """`text.normalise`, one character at a time, keeping where each output
    character came from: (normalised, start, end), end exclusive, into `text`."""
    text = text[:MAX_CHARS]
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).translate(_BN_DIGITS).lower():
            chars.append(c)
            starts.append(i)
            ends.append(i + 1)
    joined = "".join(chars)

    # Links become one token, as the model sees them; the token points at the whole link.
    out_c: list[str] = []
    out_s: list[int] = []
    out_e: list[int] = []
    last = 0
    for m in URL.finditer(joined):
        a, b = m.span()
        out_c.extend(joined[last:a])
        out_s.extend(starts[last:a])
        out_e.extend(ends[last:a])
        token = " urltoken "
        out_c.extend(token)
        out_s.extend([starts[a]] * len(token))
        out_e.extend([ends[b - 1]] * len(token))
        last = b
    out_c.extend(joined[last:])
    out_s.extend(starts[last:])
    out_e.extend(ends[last:])

    # Digits to 0, runs of space to one, no space at either end.
    norm_c: list[str] = []
    norm_s: list[int] = []
    norm_e: list[int] = []
    for c, s, e in zip(out_c, out_s, out_e, strict=True):
        if c.isspace():
            if not norm_c or norm_c[-1] == " ":
                continue
            c = " "
        norm_c.append(_DIGIT.sub("0", c))
        norm_s.append(s)
        norm_e.append(e)
    while norm_c and norm_c[-1] == " ":
        norm_c.pop()
        norm_s.pop()
        norm_e.pop()
    return "".join(norm_c), norm_s, norm_e


def _span(text: str, starts: list[int], ends: list[int], a: int, b: int) -> dict:
    lo, hi = starts[a], ends[b - 1]
    return {"text": text[lo:hi].strip(), "start": lo, "end": hi}


def cue_phrases(text: str) -> list[tuple[Cue, list[dict]]]:
    """Each scam cue the message shows, with the stretches of text that showed it."""
    norm, starts, ends = mapped(text)
    out = []
    for cue in matched(norm):
        if cue.benign:
            continue
        found: list[dict] = []
        for pattern in cue.patterns:
            for m in pattern.finditer(norm):
                if m.end() > m.start():
                    found.append(_span(text, starts, ends, m.start(), m.end()))
        found.sort(key=lambda p: p["start"])
        unique: list[dict] = []
        for phrase in found:
            if phrase["text"] and phrase not in unique:
                unique.append(phrase)
        out.append((cue, unique[:MAX_PHRASES]))
    return out


def _chars_and_weights(pipeline) -> tuple[TfidfVectorizer, float, object] | None:
    """The character n-gram vectorizer, its weight in the feature union, and the
    scam output's coefficients for those columns; None for any other kind of model."""
    try:
        union = pipeline.named_steps["features"]
        name, vectorizer = union.transformer_list[0]
        head = pipeline.named_steps["model"].estimators_[0]
    except (AttributeError, KeyError, IndexError):
        return None
    if not isinstance(vectorizer, TfidfVectorizer) or vectorizer.analyzer != "char_wb":
        return None
    weight = (union.transformer_weights or {}).get(name, 1.0)
    coef = head.coef_[0][: len(vectorizer.vocabulary_)]
    return vectorizer, weight, coef


def highlights(text: str, pipeline) -> list[dict]:
    """The words that raised the scam score most, strongest first."""
    parts = _chars_and_weights(pipeline)
    if parts is None:
        return []
    vectorizer, weight, coef = parts
    norm, starts, ends = mapped(text)
    row = vectorizer.transform([norm])
    value = dict(zip(row.indices.tolist(), row.data.tolist(), strict=True))
    analyse = vectorizer.build_analyzer()
    vocab = vectorizer.vocabulary_

    words = [(m.start(), m.end()) for m in re.finditer(r"\S+", norm)]
    grams = [Counter(g for g in analyse(norm[a:b]) if g in vocab) for a, b in words]
    total: Counter = Counter()
    for counts in grams:
        total.update(counts)

    scored = []
    for (a, b), counts in zip(words, grams, strict=True):
        score = sum(
            weight * value.get(vocab[g], 0.0) * coef[vocab[g]] * n / total[g]
            for g, n in counts.items()
        )
        if score > 0.01:
            scored.append(_span(text, starts, ends, a, b) | {"weight": round(float(score), 3)})
    scored.sort(key=lambda w: -w["weight"])
    return scored[:MAX_HIGHLIGHTS]
