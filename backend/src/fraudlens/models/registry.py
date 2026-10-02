"""Versioned model storage: artifacts/models/<version>/ plus a CURRENT pointer.

uv run python -m fraudlens.models.registry              # list the versions
uv run python -m fraudlens.models.registry promote v3   # serve v3 after a restart
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from ..config import settings
from .bundle import ModelBundle

_VERSION = re.compile(r"^v(\d+)$")


def models_dir() -> Path:
    return settings.artifacts_dir / "models"


def versions(root: Path | None = None) -> list[str]:
    root = root or models_dir()
    if not root.is_dir():
        return []
    found = [p.name for p in root.iterdir() if p.is_dir() and _VERSION.match(p.name)]
    return sorted(found, key=lambda v: int(v[1:]))


def next_version(root: Path | None = None) -> str:
    existing = versions(root)
    return f"v{int(existing[-1][1:]) + 1}" if existing else "v1"


def current_version(root: Path | None = None) -> str | None:
    pointer = (root or models_dir()) / "CURRENT"
    return pointer.read_text().strip() if pointer.is_file() else None


def promote(version: str, root: Path | None = None) -> None:
    """Make `version` the one the API serves."""
    root = root or models_dir()
    if version not in versions(root):
        raise ValueError(f"unknown model version {version!r}")
    (root / "CURRENT").write_text(version + "\n")


def load(version: str | None = None, root: Path | None = None) -> ModelBundle:
    root = root or models_dir()
    version = version or current_version(root)
    if version is None or not _VERSION.match(version):
        raise FileNotFoundError(f"no model version to load in {root}")
    return ModelBundle.load(root / version)


def main() -> None:
    parser = argparse.ArgumentParser(description="List model versions or promote one")
    parser.add_argument("command", nargs="?", choices=("list", "promote"), default="list")
    parser.add_argument("version", nargs="?")
    args = parser.parse_args()
    if args.command == "promote":
        if args.version is None:
            parser.error("promote needs a version, for example v3")
        try:
            load(args.version)  # a version that cannot be loaded is never promoted
            promote(args.version)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(f"{args.version} promoted: restart the API to serve it")
    current = current_version()
    for version in versions():
        print(version, "(promoted)" if version == current else "")


if __name__ == "__main__":
    main()
