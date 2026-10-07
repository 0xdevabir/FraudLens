"""A public, human-labelled smishing corpus, used to train and to test on text we did not write.

Source: "Bengali SMS Smishing Dataset" (Hugging Face
`shariul-islam/bengali-sms-smishing-dataset`, MIT licence), 7,005 SMS in Bangla
script, Banglish, English and code-mixed Bangla-English, each labelled `smish`,
`promo` or `normal` by its authors. It is fetched at a pinned revision and checked
against fixed SHA-256 sums, so the same bytes are used every time; nothing from it
is committed to this repository.

How it is used: `smish` is a scam, `promo` and `normal` are harmless. It has no
category labels, so it informs only the scam-or-not output, never the eight
categories. Its own train/validation/test split is kept: its test split is never
trained on and is the one external measurement in the report.

The files are third-party data. They are only ever parsed as Parquet tables of
strings; nothing in them is executed.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings

log = logging.getLogger(__name__)

REPO = "shariul-islam/bengali-sms-smishing-dataset"
REVISION = "9d7c131cc63187c54dfd069d8070776c82f0be45"
LICENCE = "MIT"
URL = f"https://huggingface.co/datasets/{REPO}"
FILES = {
    "train": ("data/train-00000-of-00001.parquet",
              "9fb92ea7e185297f4dd66442a7ed5f11ffc82d501c1ca2ea38385f430b9fe18e"),
    "val": ("data/validation-00000-of-00001.parquet",
            "a6a652087bbc328618cc39720a3ec66be53e106d9184dcf83791bc4da705a1fc"),
    "test": ("data/test-00000-of-00001.parquet",
             "2719d61aaf82b4b38e256e7e0d5ecee4a19588a7f522b863c713a435d0dffa31"),
}  # fmt: skip
# The dataset's names for how a message is written, mapped onto ours.
VARIETIES = {"Bengali": "bn", "Banglish": "bl", "English": "en", "CodeMix": "mx"}


@dataclass(frozen=True)
class External:
    text: str
    scam: bool
    lang: str  # bn | bl | en | mx
    label: str  # the dataset's own label: smish | promo | normal


def external_dir(settings: Settings) -> Path:
    return settings.data_dir / "external" / "bengali-sms-smishing"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(directory: Path, timeout: float = 60.0) -> bool:
    """Download the pinned files if they are not here yet. False when it cannot."""
    import httpx

    directory.mkdir(parents=True, exist_ok=True)
    for split, (name, digest) in FILES.items():
        path = directory / f"{split}.parquet"
        if path.exists() and _sha256(path) == digest:
            continue
        url = f"{URL}/resolve/{REVISION}/{name}"
        try:
            response = httpx.get(url, timeout=timeout, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("could not fetch %s: %s", url, exc)
            return False
        tmp = path.with_suffix(".part")
        tmp.write_bytes(response.content)
        if _sha256(tmp) != digest:
            tmp.unlink()
            log.warning("%s does not match its pinned checksum; not used", url)
            return False
        tmp.replace(path)
    return True


def load(directory: Path) -> dict[str, list[External]] | None:
    """The three splits, or None when the files are missing or not the pinned ones."""
    import pandas as pd

    out: dict[str, list[External]] = {}
    for split, (_, digest) in FILES.items():
        path = directory / f"{split}.parquet"
        if not path.exists() or _sha256(path) != digest:
            return None
        frame = pd.read_parquet(path, columns=["label", "text", "source"])
        out[split] = [
            External(str(text), label == "smish", VARIETIES[source], str(label))
            for label, text, source in frame.itertuples(index=False)
            if source in VARIETIES and isinstance(text, str) and text.strip()
        ]
    return out


def ensure(settings: Settings) -> dict[str, list[External]] | None:
    """The public corpus, fetched first if it is not here. None when offline: the
    model then trains on the written corpus alone and the report says so."""
    directory = external_dir(settings)
    found = load(directory)
    if found is None and fetch(directory):
        found = load(directory)
    return found


def provenance() -> dict:
    return {
        "name": "Bengali SMS Smishing Dataset",
        "url": URL,
        "revision": REVISION,
        "licence": LICENCE,
        "files": {split: {"path": n, "sha256": d} for split, (n, d) in FILES.items()},
        "labels": "smish = scam; promo and normal = harmless",
    }
