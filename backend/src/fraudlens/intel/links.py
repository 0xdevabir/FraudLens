"""What the links in a message point to. Fixed checks, not a model.

Nothing is fetched: a link is judged from its text alone, so checking a message
never touches the address in it. That also bounds what this can know: it sees a
wallet's name on someone else's domain, a shortener, a bare IP address, an
`.apk` download. It does not know whether an ordinary-looking domain was
registered yesterday or serves a copy of a login page.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

MAX_LINKS = 10

_TLDS = (
    "com|net|org|info|xyz|top|site|online|live|click|link|shop|app|store|club|vip|icu|"
    "bd|io|co|me|ly|cc|gd|at|be|gl|in|us|uk|ru|cn|tk|ml|ga|cf|gq|pw|biz|work|fun|page|dev"
)
# A scheme or `www.`, a bare dotted IP address with a path, or a bare domain on a known ending.
URL = re.compile(
    r"(?:https?://|www\.)[^\s<>\"']+"
    r"|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?/[^\s<>\"']*"
    rf"|\b(?:[a-z0-9-]+\.)+(?:{_TLDS})\b(?:/[^\s<>\"']*)?",
    re.IGNORECASE,
)
_SHORTENERS = frozenset(
    {
        "bit.ly",
        "tinyurl.com",
        "cutt.ly",
        "t.co",
        "rb.gy",
        "is.gd",
        "shorturl.at",
        "tiny.cc",
        "goo.gl",
        "ow.ly",
        "rebrand.ly",
        "t.ly",
        "s.id",
        "shorturl.asia",
    }  # fmt: skip
)
_SECOND_LEVEL = frozenset({"com", "net", "org", "gov", "edu", "co", "ac", "info"})
# Characters used to pass one name off as another: 0 for o, 1 for l, and so on.
_LOOKALIKE = str.maketrans("01358", "olesb")
_HIGH = frozenset({"lookalike_domain", "apk_download", "ip_address", "hidden_host"})
_RANK = {"none": 0, "caution": 1, "high": 2}


def registered_domain(host: str) -> str:
    """`pay.upaybd.com` -> `upaybd.com`; `x.nagad.com.bd` -> `nagad.com.bd`."""
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def check_link(raw: str, brands: dict[str, list[str]]) -> dict | None:
    """One link's host, what is wrong with it and how much that matters."""
    url = raw.rstrip(".,;:!?)।")
    try:
        parts = urlsplit(url if "://" in url else f"http://{url}")
        host = (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        return None
    if not host:
        return None

    official = {d for domains in brands.values() for d in domains}
    domain = registered_domain(host)
    flags: list[str] = []
    if _is_ip(host):
        flags.append("ip_address")
    elif domain in official:
        return {"host": host, "flags": ["official"], "level": "none"}
    else:
        # Compare the way a reader would: accents folded, look-alike digits read as letters.
        plain = unicodedata.normalize("NFKD", host).encode("ascii", "ignore").decode()
        plain = plain.translate(_LOOKALIKE)
        squeezed = plain.replace("-", "").replace(".", "")
        if any(brand in squeezed for brand in brands):
            flags.append("lookalike_domain")
        if host.startswith("xn--") or ".xn--" in host or not host.isascii():
            flags.append("non_latin_domain")
        if domain in _SHORTENERS:
            flags.append("shortener")
    if parts.username or parts.password:
        flags.append("hidden_host")  # https://upaybd.com@elsewhere.example
    if parts.path.lower().endswith(".apk"):
        flags.append("apk_download")

    level = "high" if _HIGH & set(flags) else "caution" if flags else "none"
    return {"host": host, "flags": flags, "level": level}


def check_links(text: str, brands: dict[str, list[str]]) -> list[dict]:
    found: dict[str, dict] = {}
    for match in URL.finditer(text):
        link = check_link(match.group(0), brands)
        if link is None:
            continue
        kept = found.get(link["host"])
        if kept is None:
            if len(found) == MAX_LINKS:
                break
            found[link["host"]] = link
        else:  # the same host twice: keep every flag seen
            kept["flags"] = sorted(set(kept["flags"]) | set(link["flags"]))
            kept["level"] = max(kept["level"], link["level"], key=_RANK.__getitem__)
    return list(found.values())


def worst(links: list[dict]) -> str:
    return max((link["level"] for link in links), key=_RANK.__getitem__, default="none")
