"""The platform content policy (``policy.json`` next to ``registry.json`` in the catalog repo).

It applies to every agent. The catalog maintainers change it with a pull request; the app
fetches it like the catalog and keeps the last good copy. Without any copy (first run while
offline) the policy bundled with the manager is used, so the guard never runs without rules.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator

from .download import fetch_bytes
from .errors import DownloadError, RegistryError

log = logging.getLogger(__name__)

MAX_POLICY_BYTES = 256 * 1024


@cache
def _validator() -> Draft202012Validator:
    text = resources.files(__package__).joinpath("schemas/policy.schema.json").read_text("utf-8")
    return Draft202012Validator(json.loads(text))


@dataclass(frozen=True)
class ContentPolicy:
    classifier_model: str
    blocked_topics: tuple[str, ...]
    scope_probes: tuple[str, ...]
    min_refusal_rate: float
    source: str  # "catalog", "cache" or "bundled", shown in the settings screen

    def to_dict(self) -> dict[str, Any]:
        return {
            "classifier_model": self.classifier_model,
            "blocked_topics": list(self.blocked_topics),
            "scope_probes": list(self.scope_probes),
            "min_refusal_rate": self.min_refusal_rate,
            "source": self.source,
        }


def parse_policy(data: Any, source: str) -> ContentPolicy:
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        details = "; ".join(
            f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
            for err in errors[:10]
        )
        raise RegistryError(f"policy.json is invalid: {details}")
    return ContentPolicy(
        classifier_model=data["classifier_model"],
        blocked_topics=tuple(data["blocked_topics"]),
        scope_probes=tuple(data["scope_probes"]),
        min_refusal_rate=float(data["min_refusal_rate"]),
        source=source,
    )


def bundled_policy() -> ContentPolicy:
    text = resources.files(__package__).joinpath("schemas/policy.default.json").read_text("utf-8")
    return parse_policy(json.loads(text), "bundled")


def policy_url_for(registry_url: str) -> str:
    """``policy.json`` sits next to ``registry.json`` (an https URL or a local path)."""
    base, sep, _name = registry_url.rpartition("/")
    if not sep:
        base, sep, _name = registry_url.rpartition("\\")
    return f"{base}{sep}policy.json" if sep else "policy.json"


class PolicyClient:
    """Fetches the policy with ``If-None-Match``; falls back to the cached, then bundled copy."""

    def __init__(
        self,
        url: str,
        cache_dir: Path,
        client: httpx.Client,
        allowed_hosts: frozenset[str],
        ttl_s: float,
    ) -> None:
        self._url = url
        self._cache_file = cache_dir / "policy.json"
        self._meta_file = cache_dir / "policy.meta.json"
        self._client = client
        self._allowed_hosts = allowed_hosts
        self._ttl_s = ttl_s
        self._current: ContentPolicy | None = None
        self._checked = 0.0
        self._refreshing = threading.Lock()

    def get(self, *, refresh: bool = False) -> ContentPolicy:
        if self._current is not None and not refresh and time.time() - self._checked < self._ttl_s:
            return self._current
        self._current = self._load()
        self._checked = time.time()
        return self._current

    def current(self) -> ContentPolicy:
        """The policy without waiting on the network (for the guard, inside the event loop).

        Before the first fetch this is the cached or bundled copy; a stale policy is
        refreshed in a background thread.
        """
        if self._current is None:
            self._current = self._read_cache() or bundled_policy()
        if time.time() - self._checked >= self._ttl_s and self._refreshing.acquire(False):
            self._checked = time.time()  # one refresh at a time, and not on every call

            def refresh() -> None:
                try:
                    self._current = self._load()
                except Exception:  # keep the guard running on the policy it has
                    log.exception("could not refresh the content policy")
                finally:
                    self._refreshing.release()

            threading.Thread(target=refresh, name="policy-refresh", daemon=True).start()
        return self._current

    def _load(self) -> ContentPolicy:
        if not self._url.startswith(("https://", "http://")):
            try:
                return parse_policy(json.loads(Path(self._url).read_text("utf-8")), "catalog")
            except FileNotFoundError:
                return self._fallback("no policy.json next to the local catalog")
            except (OSError, ValueError, RegistryError) as exc:
                return self._fallback(str(exc))

        meta = self._read_meta()
        cached = self._read_cache()
        headers = {"If-None-Match": meta["etag"]} if cached is not None and meta.get("etag") else {}
        try:
            status, body, response_headers = fetch_bytes(
                self._client,
                self._url,
                allowed_hosts=self._allowed_hosts,
                max_bytes=MAX_POLICY_BYTES,
                headers=headers,
            )
        except DownloadError as exc:
            return self._fallback(str(exc))
        if status == 304 and cached is not None:
            return cached
        if status != 200:
            return self._fallback(f"HTTP {status}")
        try:
            policy = parse_policy(json.loads(body), "catalog")
        except (ValueError, RegistryError) as exc:
            # A broken policy in the catalog must not switch the guard off: keep the last one.
            log.error("ignoring the catalog policy: %s", exc)
            return self._fallback(str(exc))
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_file.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(self._cache_file)
        self._meta_file.write_text(json.dumps({"etag": response_headers.get("etag")}), "utf-8")
        return policy

    def _fallback(self, reason: str) -> ContentPolicy:
        cached = self._read_cache()
        if cached is not None:
            log.warning("using the cached content policy: %s", reason)
            return cached
        log.warning("using the bundled content policy: %s", reason)
        return bundled_policy()

    def _read_cache(self) -> ContentPolicy | None:
        try:
            return parse_policy(json.loads(self._cache_file.read_text("utf-8")), "cache")
        except (OSError, ValueError, RegistryError):
            return None

    def _read_meta(self) -> dict[str, Any]:
        try:
            meta: dict[str, Any] = json.loads(self._meta_file.read_text("utf-8"))
            return meta
        except (OSError, ValueError):
            return {}
