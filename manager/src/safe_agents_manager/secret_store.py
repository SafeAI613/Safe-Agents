"""Where agent secrets come from and where they are kept.

Secrets live in the operating system's credential store (Windows Credential Manager,
macOS Keychain, Secret Service on Linux) through the ``keyring`` library. They are never
written to disk by the manager, never logged and never returned by the API.

Two scopes:

* global keys (``GLOBAL_KEYS``) are shared by all agents, e.g. the OpenRouter key;
* any other key belongs to one agent (``<agent_id>:<KEY>``), so two agents may both declare
  ``FOO_TOKEN`` without seeing each other's value. A global value of the same name is
  used as a fallback.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from typing import Protocol

from .errors import SecretStoreError

KEYRING_SERVICE = "SafeAgents"
GLOBAL_KEYS = frozenset({"OPENROUTER_API_KEY", "LANGSMITH_API_KEY"})


class SecretProvider(Protocol):
    def get(self, key: str, agent_id: str | None = None) -> str | None: ...


class SecretBackend(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class EnvSecretProvider:
    """Reads secrets from environment variables (scope is ignored). Used by the CLI."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def get(self, key: str, agent_id: str | None = None) -> str | None:
        return self._environ.get(key) or None


class StaticSecretProvider:
    """Fixed values; ``"<agent_id>:<KEY>"`` entries override plain ``KEY`` ones. For tests."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def get(self, key: str, agent_id: str | None = None) -> str | None:
        if agent_id and (value := self._values.get(f"{agent_id}:{key}")):
            return value
        return self._values.get(key) or None


class MemoryBackend:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> str | None:
        with self._lock:
            return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        with self._lock:
            self._values[name] = value

    def delete(self, name: str) -> None:
        with self._lock:
            self._values.pop(name, None)


class KeyringBackend:
    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        import keyring

        self._keyring = keyring
        self._service = service

    def _call(self, action: str, *args: str) -> str | None:
        from keyring.errors import KeyringError, PasswordDeleteError

        try:
            result: str | None = getattr(self._keyring, action)(self._service, *args)
            return result
        except PasswordDeleteError:
            return None  # deleting a secret that is not set is fine
        except KeyringError as exc:
            raise SecretStoreError(f"the system credential store is unavailable: {exc}") from exc

    def get(self, name: str) -> str | None:
        return self._call("get_password", name)

    def set(self, name: str, value: str) -> None:
        self._call("set_password", name, value)

    def delete(self, name: str) -> None:
        self._call("delete_password", name)


def _scoped_name(key: str, agent_id: str | None) -> str:
    if key in GLOBAL_KEYS or agent_id is None:
        return f"global:{key}"
    return f"{agent_id}:{key}"


class SecretStore:
    """Readable (``SecretProvider``) and writable store over a backend."""

    def __init__(self, backend: SecretBackend, fallback: SecretProvider | None = None) -> None:
        self._backend = backend
        self._fallback = fallback

    def get(self, key: str, agent_id: str | None = None) -> str | None:
        if agent_id and key not in GLOBAL_KEYS:
            value = self._backend.get(_scoped_name(key, agent_id))
            if value:
                return value
        value = self._backend.get(_scoped_name(key, None))
        if value:
            return value
        return self._fallback.get(key, agent_id) if self._fallback else None

    def is_set_in_scope(self, key: str, agent_id: str | None = None) -> bool:
        return bool(self._backend.get(_scoped_name(key, agent_id)))

    def set(self, key: str, value: str, agent_id: str | None = None) -> None:
        if not value:
            raise ValueError("use delete() to remove a secret")
        self._backend.set(_scoped_name(key, agent_id), value)

    def delete(self, key: str, agent_id: str | None = None) -> None:
        self._backend.delete(_scoped_name(key, agent_id))

    def delete_agent(self, agent_id: str, keys: list[str]) -> None:
        for key in keys:
            if key not in GLOBAL_KEYS:
                self._backend.delete(_scoped_name(key, agent_id))
