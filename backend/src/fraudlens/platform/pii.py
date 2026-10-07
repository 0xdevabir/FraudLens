"""Masking of personal data in text a customer typed.

Wallet and device identifiers are structured fields and are masked where they
are shown. Free text is different: a customer describing a scam will often type
a phone number or an account number. That text is stored as written and masked
whenever it leaves the database.
"""

from __future__ import annotations

import re

_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Phone, card and account numbers: 10 to 19 digits, written with spaces or dashes or not.
_NUMBER = re.compile(r"(?<![\w.])\+?\d(?:[ -]?\d){9,18}(?![\w])")


def redact(text: str | None) -> str | None:
    """`text` with e-mail addresses and long numbers masked. Bangla digits count as digits."""
    if not text:
        return text
    # Same length after translation, so positions found in one apply to the other.
    plain = text.translate(_DIGITS)
    out, last = [], 0
    spans = [(m.start(), m.end(), "[email]") for m in _EMAIL.finditer(plain)]
    spans += [(m.start(), m.end(), "[number]") for m in _NUMBER.finditer(plain)]
    for start, end, label in sorted(spans):
        if start < last:
            continue
        out += [text[last:start], label]
        last = end
    return "".join(out) + text[last:]


def mask_id(value: str | None) -> str | None:
    """A wallet or agent identifier as the console shows it: `W***6128`."""
    if not value:
        return value
    return f"{value[0]}***" if len(value) <= 5 else f"{value[0]}***{value[-4:]}"
