"""Settings from files and from a secrets store, so a secret never has to sit in the environment.

Two sources, after the environment and before `.env`:

- **Files.** `FRAUDLENS_<NAME>_FILE=/run/secrets/jwt_secret` reads `<name>` from that
  file (one trailing newline removed). This is the Docker and Kubernetes secrets
  convention: the orchestrator mounts the file, the environment only names it.
  Setting both `FRAUDLENS_<NAME>` and `FRAUDLENS_<NAME>_FILE` is refused.
- **A provider.** `FRAUDLENS_SECRETS_PROVIDER=package.module:function` is called once
  with the names of the settings still unset and returns a mapping of the ones it
  knows, e.g. a function that reads Vault or AWS Secrets Manager. Nothing here
  depends on any of those clients; the function brings its own.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

PREFIX = "FRAUDLENS_"
PROVIDER_VAR = PREFIX + "SECRETS_PROVIDER"
MAX_SECRET_BYTES = 64_000

Provider = Callable[[list[str]], Mapping[str, str]]


def read_secret_file(path: str | Path) -> str:
    data = Path(path).read_bytes()
    if len(data) > MAX_SECRET_BYTES:
        raise ValueError(f"{path} is larger than a secret should be")
    text = data.decode()
    return text[:-1] if text.endswith("\n") else text


def load_provider(spec: str) -> Provider:
    module, _, name = spec.partition(":")
    if not module or not name:
        raise ValueError(f"{PROVIDER_VAR} must look like 'package.module:function', not {spec!r}")
    provider = getattr(importlib.import_module(module), name)
    if not callable(provider):
        raise TypeError(f"{spec} is not callable")
    return provider


class SecretFilesSource(PydanticBaseSettingsSource):
    """`FRAUDLENS_<NAME>_FILE` and the optional provider, for every setting."""

    def __init__(self, settings_cls: type[BaseSettings], environ: Mapping[str, str] | None = None):
        super().__init__(settings_cls)
        self.environ = os.environ if environ is None else environ

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False  # everything is done in __call__

    def _value(self, name: str, field: FieldInfo, raw: str) -> Any:
        return self.prepare_field_value(name, field, raw, self.field_is_complex(field))

    def __call__(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        fields = self.settings_cls.model_fields
        for name, field in fields.items():
            path = self.environ.get(f"{PREFIX}{name.upper()}_FILE")
            if not path:
                continue
            if f"{PREFIX}{name.upper()}" in self.environ:
                raise ValueError(f"set {PREFIX}{name.upper()} or its _FILE variant, not both")
            values[name] = self._value(name, field, read_secret_file(path))
        spec = self.environ.get(PROVIDER_VAR)
        if spec:
            unset = [
                n for n in fields if n not in values and f"{PREFIX}{n.upper()}" not in self.environ
            ]
            for name, raw in load_provider(spec)(unset).items():
                if name in unset and raw is not None:
                    values[name] = self._value(name, fields[name], str(raw))
        return values


def directory_provider(names: list[str]) -> dict[str, str]:
    """An example provider: one file per setting in `FRAUDLENS_SECRETS_DIR` (default
    /run/secrets), named after the setting. Swap in a function that asks your vault."""
    root = Path(os.environ.get(PREFIX + "SECRETS_DIR", "/run/secrets"))
    found = {}
    for name in names:
        path = root / name
        if path.is_file():
            found[name] = read_secret_file(path)
    return found
