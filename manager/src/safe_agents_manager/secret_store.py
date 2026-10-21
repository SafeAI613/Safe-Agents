"""Where agent secrets come from.

In M1 secrets are read from the manager's own environment. In M3 the desktop shell will
provide a keychain-backed implementation of the same protocol.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol


class SecretProvider(Protocol):
    def get(self, key: str) -> str | None: ...


class EnvSecretProvider:
    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def get(self, key: str) -> str | None:
        value = self._environ.get(key)
        return value or None


class StaticSecretProvider:
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def get(self, key: str) -> str | None:
        return self._values.get(key) or None
