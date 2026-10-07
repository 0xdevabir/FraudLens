"""Check a message a customer received: is it a scam, of which kind, and why.

Two independent readings are combined and the stronger one wins: the classifier
reads the words (`text.py`), the link check reads where the links go (`links.py`).
The text is read and discarded. It is not stored, not logged and, like every
other piece of customer free text in the platform, never treated as an instruction.

The reasons point into the message itself (`explain.py`): each signal carries the
phrases that matched it, and `highlights` names the words that raised the score most.
"""

from __future__ import annotations

from collections.abc import Sequence

from .explain import cue_phrases, highlights
from .links import check_links, worst
from .taxonomy import Taxonomy
from .text import TextModel

_RANK = {"none": 0, "caution": 1, "high": 2}
# Which category a link flag is evidence of, whatever the words around it say.
_LINK_CATEGORIES = {
    "lookalike_domain": ("phishing_malware", "impersonation"),
    "apk_download": ("phishing_malware",),
    "ip_address": ("phishing_malware",),
    "hidden_host": ("phishing_malware",),
}


def check_message(
    text: str, model: TextModel | None, taxonomy: Taxonomy, listed: Sequence[str] = ()
) -> dict:
    """`model` is None when the classifier has not been trained; links are still checked.

    `listed` names the kinds ('phone', 'url') of blocklist entries the message mentions:
    a phone number or domain people have already been scammed through is high risk
    whatever the words say. The entries themselves are never returned.
    """
    links = check_links(text, taxonomy.brands)
    level, risk, named, signals, words = worst(links), None, {}, [], []
    if listed:
        level = "high"

    if model is not None:
        scores = model.score([text])[0]
        risk = round(float(scores[0]), 4)
        text_level = model.level(risk)
        if text_level != "none":
            # Each cue with the stretch of the customer's own text that showed it.
            signals = [
                {"id": cue.id, "label": cue.label, "phrases": phrases}
                for cue, phrases in cue_phrases(text)
            ]
            words = highlights(text, model.pipeline)
            for cid, p in zip(model.categories, scores[1:], strict=True):
                if p >= model.thresholds["category"]:
                    named[cid] = {"probability": round(float(p), 4), "source": "model"}
        level = max(level, text_level, key=_RANK.__getitem__)

    for link in links:
        for flag in link["flags"]:
            for cid in _LINK_CATEGORIES.get(flag, ()):
                named.setdefault(cid, {"probability": None, "source": "link"})

    # Most likely first; a category known only from a link has no probability and goes last.
    order = sorted(named, key=lambda cid: -(named[cid]["probability"] or 0.0))
    categories = []
    for cid in order:
        category = taxonomy.category(cid)
        categories.append(
            {"id": cid, "number": category.number, "name": category.name.model_dump()} | named[cid]
        )

    advice = None
    if level != "none":
        top = taxonomy.category(order[0]).advice if order else taxonomy.general_advice
        advice = top.model_dump()
    return {
        "level": level,
        "risk": risk,
        "categories": categories,
        "signals": signals,
        "highlights": words,
        "links": links,
        "blocklist": list(listed),
        "advice": advice,
        "model_version": model.version if model is not None else None,
    }
