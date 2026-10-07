"""Synthetic scam and harmless messages, generated from the scripts in `corpus.yaml`.

There is no public corpus of scam messages sent to Bangladeshi wallet customers,
and real ones are personal data. So the classifier learns from scripts written
for this project and varied here: different amounts, numbers, names and brands,
the spellings people use when they write Bangla in Latin letters, greetings,
typos. That makes a model that can be measured honestly on what it was given,
and nothing more: see `docs/FRAUD_TAXONOMY.md` for what that does not prove.

Three kinds of test are built in:

- `val` and `test` rows of a trained family use templates the model never saw,
  so they measure new wording of a known script;
- `unseen` rows come from families held out entirely, so they measure a script
  the model has never met.
"""

from __future__ import annotations

import random
import re
import zlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from .taxonomy import load_taxonomy

CORPUS_PATH = Path(__file__).parent / "corpus.yaml"
LANGUAGES = {
    "bn": "Bangla",
    "bl": "Bangla in Latin letters",
    "en": "English",
    "mx": "Bangla and English mixed",
}
# The scam typologies the corpus is organised around (the scripts customers meet
# most often), so results can be read per typology. `other` covers the rest.
TYPOLOGIES = (
    "fake_agent_helpline", "prize_lottery", "wrong_number", "job_investment",
    "govt_aid", "otp_pin_phishing", "other",
)  # fmt: skip

_BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")

_BRANDS = ["upay", "bKash", "Nagad", "Rocket", "Upay", "bkash", "nagad", "bikash", "nogod"]
_BRANDS_BN = ["উপায়", "বিকাশ", "নগদ", "রকেট"]
_NAMES = ["Rahim", "Karim", "Sumi", "Rakib", "Nusrat", "Hasan", "Mim", "Sabbir", "Tania", "Jahid"]
_NAMES_BN = ["রহিম", "করিম", "সুমি", "রাকিব", "নুসরাত", "হাসান", "মিম", "সাব্বির", "তানিয়া"]
_ITEMS = ["iPhone 13", "Redmi Note 12", "three piece", "sharee", "motorcycle", "laptop",
          "smart TV", "panjabi", "air cooler", "blender"]  # fmt: skip
_ITEMS_BN = ["আইফোন", "স্মার্টফোন", "থ্রি-পিস", "শাড়ি", "মোটরসাইকেল", "ল্যাপটপ", "টিভি", "পাঞ্জাবি"]
_BIG = ["5 lakh", "10 lakh", "25 lakh", "50,000", "2 lakh", "1 crore", "3,00,000"]
_BIG_BN = ["৫ লক্ষ", "১০ লক্ষ", "২৫ লক্ষ", "৫০ হাজার", "২ লক্ষ", "১ কোটি"]
_BAD_HOSTS = ["upay-verify.xyz", "bkash-offer.top", "nagad-kyc.site", "upaybd-login.online",
              "bkash.account-update.info", "secure-nagad.live", "bit.ly", "tinyurl.com",
              "cutt.ly", "upay-bonus.click", "wallet-helpdesk.xyz", "192.168.4.21"]  # fmt: skip
_BAD_PATHS = ["/login", "/kyc", "/verify", "/3xQz9", "/offer", "/update", "/a8Kd2", "/pay"]
_OK_LINKS = ["https://www.upaybd.com/offers", "https://www.bkash.com/campaign",
             "https://nagad.com.bd/offers", "upaybd.com/offer"]  # fmt: skip
_WEB_LINKS = ["https://www.youtube.com/watch?v=k3Zx9", "https://www.daraz.com.bd/track/88213",
              "https://www.prothomalo.com/sports", "https://forms.gle/Zr8Qe", "youtu.be/q9Lm2",
              "https://www.facebook.com/photo/9912"]  # fmt: skip

# Used on scams and harmless text alike, so neither is a giveaway.
_OPENERS = {
    "bn": ["আসসালামু আলাইকুম। ", "ভাই, ", "প্রিয় গ্রাহক, ", "স্যার, ", "আপা, "],
    "bl": ["Assalamu alaikum. ", "vai ", "Sir ", "apu ", "hello ", "bhai, "],
    "en": ["Hello. ", "Dear customer, ", "Hi, ", "Sir, ", "Good day. "],
    "mx": ["Hello ভাই, ", "Dear customer, ", "Sir, ", "আপু, ", "Hi, "],
}
_CLOSERS = {
    "bn": [" ধন্যবাদ।", " ভালো থাকবেন।", ""],
    "bl": [" dhonnobad", " thanks", " pls", ""],
    "en": [" Thanks.", " Thank you.", " Regards.", ""],
    "mx": [" Thanks.", " ধন্যবাদ।", " please", ""],
}

# How the same Bangla word is spelled by different people writing in Latin letters.
_SPELLINGS = {
    "taka": ["tk", "takaa", "taaka"],
    "apnar": ["apner", "apnr", "apnaar"],
    "apni": ["apne", "apny"],
    "korun": ["koren", "krun", "korben"],
    "bolun": ["bolen", "bolben"],
    "bolchi": ["bolsi", "boltesi", "bolchhi"],
    "theke": ["thk", "thake", "theike"],
    "hoye": ["hoe", "hoia"],
    "jabe": ["jbe", "jaabe"],
    "vul": ["bhul", "bul"],
    "kore": ["kre", "koira"],
    "number": ["nambar", "no", "nmbr"],
    "account": ["akaunt", "acc", "a/c", "ekaunt"],
    "din": ["den", "diben"],
    "hobe": ["hbe", "hoibe"],
    "ase": ["ache", "achhe"],
    "korte": ["krte", "korthe"],
    "jonno": ["jnno", "jonne"],
    "amar": ["amr", "amaar"],
    "ekta": ["akta", "1ta"],
    "ei": ["ai", "ey"],
    "code": ["kod", "cod"],
    "ferot": ["ferat", "back"],
    "geche": ["gese", "gechhe"],
    "ekhon": ["akhon", "ekhn"],
    "ekhoni": ["akhoni", "ekhuni"],
    "taratari": ["tartari", "taratari"],
    "pathan": ["pathaan", "pathiye den", "send koren"],
    "bolen": ["bolun", "bolen na"],
    "korsi": ["korchi", "korechi"],
    "gese": ["geche", "gechhe"],
}

_SLOT = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class Family:
    id: str
    labels: tuple[str, ...]
    templates: tuple[tuple[str, str], ...]  # (language, text)
    hard: bool = False
    held_out: bool = False
    typology: str | None = None  # scams only: one of TYPOLOGIES


@dataclass(frozen=True)
class Sample:
    text: str
    labels: tuple[str, ...]
    family: str
    lang: str
    split: str  # train | val | test | unseen
    hard: bool
    typology: str | None = None


@cache
def load_families(path: Path = CORPUS_PATH) -> tuple[Family, ...]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["families"]
    known = set(load_taxonomy().ids)
    families = []
    for item in raw:
        family = Family(
            id=item["id"],
            labels=tuple(item["labels"]),
            templates=tuple((lang, text) for lang, text in item["templates"]),
            hard=bool(item.get("hard", False)),
            held_out=bool(item.get("held_out", False)),
            typology=item.get("typology", "other") if item["labels"] else None,
        )
        if set(family.labels) - known:
            raise ValueError(f"family {family.id} names an unknown category")
        if family.typology is not None and family.typology not in TYPOLOGIES:
            raise ValueError(f"family {family.id} names an unknown typology")
        if any(lang not in LANGUAGES for lang, _ in family.templates):
            raise ValueError(f"family {family.id} has a template in an unknown language")
        families.append(family)
    if len({f.id for f in families}) != len(families):
        raise ValueError("family ids must be unique")
    return tuple(families)


def template_split(family: Family, text: str) -> str:
    """Which side of the experiment a template is on. Depends only on its text, so
    adding a template never moves another one."""
    if family.held_out:
        return "unseen"
    bucket = zlib.crc32(text.encode("utf-8")) % 10
    return "train" if bucket < 6 else "val" if bucket < 8 else "test"


def _amount(rng: random.Random, low: int, high: int) -> str:
    value = rng.choice([50, 100, 500]) * rng.randint(low, high)
    return f"{value:,}" if rng.random() < 0.4 else str(value)


def _fill(rng: random.Random, name: str) -> str:
    bn = name.endswith("_bn")
    match name.removesuffix("_bn"):
        case "brand":
            return rng.choice(_BRANDS_BN if bn else _BRANDS)
        case "name":
            return rng.choice(_NAMES_BN if bn else _NAMES)
        case "item":
            return rng.choice(_ITEMS_BN if bn else _ITEMS)
        case "big":
            return rng.choice(_BIG_BN if bn else _BIG)
        case "amt":
            value = _amount(rng, 4, 90)
        case "fee":
            value = _amount(rng, 1, 12)
        case "num":
            value = f"01{rng.choice('3456789')}{rng.randrange(10**8):08d}"
        case "otp":
            value = f"{rng.randrange(10**6):06d}"
        case "pct":
            value = str(rng.choice([2, 3, 5, 8, 10, 40, 50, 60, 70]))
        case "trx":
            return "".join(rng.choices("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789", k=10))
        case "bad":
            scheme = rng.choice(["http://", "https://", ""])
            return scheme + rng.choice(_BAD_HOSTS) + rng.choice(_BAD_PATHS)
        case "apk":
            return "http://" + rng.choice(_BAD_HOSTS[:6]) + rng.choice(["/upay.apk", "/app.apk"])
        case "ok":
            return rng.choice(_OK_LINKS)
        case "web":
            return rng.choice(_WEB_LINKS)
        case _:
            raise KeyError(f"unknown slot {{{name}}}")
    return value.translate(_BN_DIGITS) if bn else value


def _respell(rng: random.Random, text: str) -> str:
    words = text.split(" ")
    for i, word in enumerate(words):
        options = _SPELLINGS.get(word.lower())
        if options and rng.random() < 0.3:
            words[i] = rng.choice(options)
    return " ".join(words)


def _typos(rng: random.Random, text: str) -> str:
    """Drop or double the odd Latin letter, the way thumbs do."""
    out = []
    for ch in text:
        if ch.isascii() and ch.isalpha() and rng.random() < 0.012:
            if rng.random() < 0.5:
                continue
            out.append(ch)
        out.append(ch)
    return "".join(out)


def render(rng: random.Random, lang: str, template: str) -> str:
    # Links are kept apart so a typo or a case change never breaks one.
    links: list[str] = []

    def fill(match: re.Match) -> str:
        name = match.group(1)
        value = _fill(rng, name)
        if name in ("bad", "apk", "ok", "web"):
            links.append(value)
            return f"\x00{len(links) - 1}\x00"
        return value

    text = _SLOT.sub(fill, template)
    if lang == "bl":
        text = _respell(rng, text)
    if rng.random() < 0.3:
        text = rng.choice(_OPENERS[lang]) + text
    if rng.random() < 0.25:
        text = text + rng.choice(_CLOSERS[lang])
    if rng.random() < 0.5:
        text = _typos(rng, text)
    style = rng.random()
    if style < 0.15:
        text = text.lower()
    elif style < 0.2:
        text = text.upper()
    elif style < 0.3:
        text = text.replace(".", "").replace(",", "")
    return re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], text)


def generate(seed: int = 7, per_template: int = 30) -> list[Sample]:
    """Every family's templates rendered `per_template` times. Same seed, same corpus."""
    rng = random.Random(seed)
    samples = []
    for family in load_families():
        for lang, template in family.templates:
            split = template_split(family, template)
            seen: set[str] = set()
            for _ in range(per_template):
                text = render(rng, lang, template)
                if text in seen:  # a template with no slots renders few distinct texts
                    continue
                seen.add(text)
                samples.append(
                    Sample(
                        text, family.labels, family.id, lang, split, family.hard, family.typology
                    )
                )
    return samples
