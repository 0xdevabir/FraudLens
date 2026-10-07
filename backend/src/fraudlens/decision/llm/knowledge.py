"""The knowledge base a language model reads before writing a case note.

`knowledge.yaml` explains the platform's words in English and Bangla: tiers, how a
decision was reached, risk bands, scam types, reason codes. It is context, not
evidence, so it may contain no digits at all: the grounding check accepts only
numbers found in the evidence, and a number copied from here would be one.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..narrative import _BAND, _TYPOLOGY_BN
from ..policy import TIERS
from ..reasons import RULE_CODES

KNOWLEDGE_PATH = Path(__file__).with_name("knowledge.yaml")
LANGS = ("en", "bn")
# A prompt budget: the knowledge base must leave room for the evidence.
MAX_CHARS = 12_000
_DIGIT = re.compile(r"[0-9০-৯]")

# Section: the ids it may use (None: any id).
_SECTIONS: dict[str, frozenset[str] | None] = {
    "tiers": frozenset(TIERS),
    "decided_by": frozenset({"model", "rule", "rules_only"}),
    "risk_bands": frozenset(_BAND),
    "typologies": frozenset(_TYPOLOGY_BN),
    "reasons": frozenset(RULE_CODES),
    "terms": None,
}
_HEADINGS = {
    "en": {
        "context": "About FraudLens",
        "tiers": "Decision tiers",
        "decided_by": "How the decision was reached",
        "risk_bands": "Risk bands",
        "typologies": "Scam types",
        "reasons": "Reason codes",
        "terms": "Terms",
        "style": "How to write",
    },
    "bn": {
        "context": "ফ্রডলেন্স সম্পর্কে",
        "tiers": "সিদ্ধান্তের স্তর",
        "decided_by": "সিদ্ধান্ত কীভাবে হয়েছে",
        "risk_bands": "ঝুঁকির মাত্রা",
        "typologies": "প্রতারণার ধরন",
        "reasons": "কারণের কোড",
        "terms": "পরিভাষা",
        "style": "কীভাবে লিখবেন",
    },
}


class KnowledgeError(ValueError):
    pass


@dataclass(frozen=True)
class Knowledge:
    version: str  # a hash of the file: changes whenever the text does
    texts: dict[str, str]  # language -> the rendered knowledge base

    def render(self, lang: str) -> str:
        return self.texts[lang]


def _pair(where: str, node) -> dict[str, str]:
    if not isinstance(node, dict) or set(node) != set(LANGS):
        raise KnowledgeError(f"{where}: needs exactly an 'en' and a 'bn' text")
    for lang in LANGS:
        text = node[lang]
        if not isinstance(text, str) or not text.strip():
            raise KnowledgeError(f"{where}.{lang}: empty")
        if _DIGIT.search(text):
            raise KnowledgeError(f"{where}.{lang}: contains a digit")
    return {lang: " ".join(node[lang].split()) for lang in LANGS}


def parse(raw: str) -> Knowledge:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise KnowledgeError(f"not valid YAML: {exc}") from exc
    expected = {"context", "style", *_SECTIONS}
    if not isinstance(data, dict) or set(data) != expected:
        found = sorted(data) if isinstance(data, dict) else type(data).__name__
        raise KnowledgeError(f"top-level sections must be {sorted(expected)}, not {found}")

    lines: dict[str, list[str]] = {lang: [] for lang in LANGS}
    for section in ("context", *_SECTIONS, "style"):
        node = data[section]
        if section in ("context", "style"):
            pair = _pair(section, node)
            for lang in LANGS:
                lines[lang] += [f"## {_HEADINGS[lang][section]}", pair[lang], ""]
            continue
        allowed = _SECTIONS[section]
        if not isinstance(node, dict) or not node:
            raise KnowledgeError(f"{section}: needs at least one entry")
        if allowed is not None and (unknown := set(node) - allowed):
            raise KnowledgeError(f"{section}: unknown ids {sorted(unknown)}")
        for lang in LANGS:
            lines[lang].append(f"## {_HEADINGS[lang][section]}")
        for key, entry in node.items():
            if _DIGIT.search(str(key)):
                raise KnowledgeError(f"{section}.{key}: an id may not contain a digit")
            pair = _pair(f"{section}.{key}", entry)
            for lang in LANGS:
                lines[lang].append(f"- {key}: {pair[lang]}")
        for lang in LANGS:
            lines[lang].append("")

    texts = {lang: "\n".join(lines[lang]).strip() for lang in LANGS}
    for lang, text in texts.items():
        if len(text) > MAX_CHARS:
            raise KnowledgeError(f"the {lang} knowledge base is longer than {MAX_CHARS} characters")
    version = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return Knowledge(version=version, texts=texts)


def load_knowledge(path: Path = KNOWLEDGE_PATH) -> Knowledge:
    return parse(path.read_text(encoding="utf-8"))
