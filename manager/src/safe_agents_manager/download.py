"""HTTPS downloads restricted to an allow-list of hosts, with size limits and hashing.

Redirects are followed by hand so that every hop is checked against the allow-list
(GitHub release links redirect from github.com to release-assets.githubusercontent.com).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .errors import ArchiveError, DownloadError, UnsafeUrlError

ProgressFn = Callable[[int, int | None], None]
_REDIRECTS = {301, 302, 303, 307, 308}
_CHUNK = 256 * 1024


def check_url(url: str, allowed_hosts: frozenset[str]) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnsafeUrlError(f"only https URLs are allowed: {url}")
    host = (parts.hostname or "").lower()
    if host not in allowed_hosts:
        raise UnsafeUrlError(f"host '{host}' is not in the list of allowed hosts")
    if parts.username or parts.password:
        raise UnsafeUrlError("URLs with embedded credentials are not allowed")


@contextmanager
def _open(
    client: httpx.Client,
    url: str,
    allowed_hosts: frozenset[str],
    headers: dict[str, str] | None = None,
    max_redirects: int = 5,
) -> Iterator[httpx.Response]:
    for _ in range(max_redirects + 1):
        check_url(url, allowed_hosts)
        try:
            request = client.build_request("GET", url, headers=headers)
            response = client.send(request, stream=True, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise DownloadError(f"could not reach {urlsplit(url).hostname}: {exc}") from exc
        if response.status_code in _REDIRECTS and "location" in response.headers:
            response.close()
            url = urljoin(url, response.headers["location"])
            continue
        try:
            yield response
        finally:
            response.close()
        return
    raise DownloadError(f"too many redirects for {url}")


def _declared_length(response: httpx.Response) -> int | None:
    with suppress(KeyError, ValueError):
        return int(response.headers["content-length"])
    return None


def download_file(
    client: httpx.Client,
    url: str,
    dest: Path,
    *,
    allowed_hosts: frozenset[str],
    max_bytes: int,
    expected_sha256: str | None = None,
    on_progress: ProgressFn | None = None,
) -> str:
    """Stream ``url`` into ``dest`` and return its sha256. ``dest`` only appears when complete."""
    partial = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    try:
        with _open(client, url, allowed_hosts) as response:
            if response.status_code != 200:
                raise DownloadError(f"download failed with HTTP {response.status_code}: {url}")
            total = _declared_length(response)
            if total is not None and total > max_bytes:
                raise DownloadError(f"file is larger than the {max_bytes} byte limit")
            received = 0
            dest.parent.mkdir(parents=True, exist_ok=True)
            with partial.open("wb") as out:
                try:
                    for chunk in response.iter_bytes(_CHUNK):
                        received += len(chunk)
                        if received > max_bytes:
                            raise DownloadError(f"file is larger than the {max_bytes} byte limit")
                        digest.update(chunk)
                        out.write(chunk)
                        if on_progress:
                            on_progress(received, total)
                except httpx.HTTPError as exc:
                    raise DownloadError(f"download interrupted: {exc}") from exc
        actual = digest.hexdigest()
        if expected_sha256 and not hmac.compare_digest(actual, expected_sha256.lower()):
            raise ArchiveError(f"sha256 mismatch: expected {expected_sha256}, got {actual}")
        partial.replace(dest)
        return actual
    finally:
        partial.unlink(missing_ok=True)


def fetch_bytes(
    client: httpx.Client,
    url: str,
    *,
    allowed_hosts: frozenset[str],
    max_bytes: int,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, httpx.Headers]:
    """Small in-memory GET (catalog, manifests). Returns status, body and headers."""
    with _open(client, url, allowed_hosts, headers=headers) as response:
        body = bytearray()
        try:
            for chunk in response.iter_bytes(_CHUNK):
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise DownloadError(f"response from {url} exceeds {max_bytes} bytes")
        except httpx.HTTPError as exc:
            raise DownloadError(f"download interrupted: {exc}") from exc
        return response.status_code, bytes(body), response.headers
