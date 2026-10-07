"""Download the public datasets used for external validation, and verify them.

    uv run python -m fraudlens.external.download            # both
    uv run python -m fraudlens.external.download paysim

Files land in backend/data/external/<name>/ (gitignored). A file already present
with the right checksum is not downloaded again; a partial file is resumed; a file
with the wrong checksum is an error, never silently used.

Sources (no account needed):

- PaySim: the Kaggle file (ealaxi/paysim1, "PS_20174392719_1491204439457_log.csv")
  as mirrored on Hugging Face. The SHA-256 below is the one Hugging Face publishes
  for the LFS object; it has 6,362,620 rows, the size of the original release.
- Bank Account Fraud, Base variant (Jesus et al., NeurIPS 2022): the OpenML copy
  (dataset 46793, "BAF_base", version 2) as parquet. OpenML publishes no SHA-256
  for the parquet file, so the value below is the one measured on first download
  (2026-10-07); the content check (1,000,000 rows, 11,029 frauds) matches the paper.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import settings


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    file: str
    sha256: str
    size: int
    citation: str


SOURCES = {
    "paysim": Source(
        name="paysim",
        url="https://huggingface.co/datasets/theman10/paysim/resolve/main/paysim.csv",
        file="paysim.csv",
        sha256="16910f90577b0d981bf8ff289714510bb89bc71bff7d3f220f024e287e4eea6b",
        size=493_534_783,
        citation=(
            "E. A. Lopez-Rojas, A. Elmir, S. Axelsson. PaySim: A financial mobile money "
            "simulator for fraud detection. EMSS 2016."
        ),
    ),
    "baf": Source(
        name="baf",
        url="https://data.openml.org/datasets/0004/46793/dataset_46793.pq",
        file="baf_base.parquet",
        sha256="217e2fbd62a0f501c05ece5560841bdba6f4d7a3d302383c662c4d9efb22a872",
        size=69_498_475,
        citation=(
            "S. Jesus et al. Turning the Tables: Biased, Imbalanced, Dynamic Tabular "
            "Datasets for ML Evaluation. NeurIPS 2022 Datasets and Benchmarks."
        ),
    ),
}


def external_dir() -> Path:
    return settings.data_dir / "external"


def path_of(name: str) -> Path:
    source = SOURCES[name]
    return external_dir() / source.name / source.file


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(name: str) -> Path:
    """The verified local file, downloading or resuming it if needed."""
    source = SOURCES[name]
    target = path_of(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not (target.exists() and target.stat().st_size == source.size):
        if shutil.which("curl") is None:
            raise RuntimeError("curl is needed to download the external datasets")
        print(f"downloading {name} ({source.size / 1e6:.0f} MB) from {source.url}")
        for _ in range(5):  # the mirrors sometimes close long transfers; resume
            subprocess.run(
                ["curl", "-sSL", "--retry", "3", "-C", "-", "-o", str(target), source.url],
                check=False,
            )
            if target.exists() and target.stat().st_size >= source.size:
                break
    actual = sha256_of(target)
    if actual != source.sha256:
        raise RuntimeError(
            f"{target}: SHA-256 {actual} does not match the expected {source.sha256}. "
            "Delete the file and run again; if it persists the mirror has changed."
        )
    print(f"{name}: {target} ok (sha256 {actual[:12]}...)")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and verify the external datasets")
    parser.add_argument("names", nargs="*", help=f"any of {', '.join(SOURCES)}; default: all")
    names = parser.parse_args().names or list(SOURCES)
    if unknown := set(names) - set(SOURCES):
        parser.error(f"unknown dataset: {', '.join(sorted(unknown))}")
    for name in names:
        fetch(name)


if __name__ == "__main__":
    main()
