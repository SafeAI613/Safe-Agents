from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from conftest import CODELOAD_URL, SOURCE_URL, FakeGitHub

from safe_agents_manager.config import DEFAULT_ALLOWED_HOSTS as HOSTS
from safe_agents_manager.download import check_url, download_file, fetch_bytes
from safe_agents_manager.errors import ArchiveError, DownloadError, UnsafeUrlError


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/foo.zip",
        "https://evil.example/foo.zip",
        "https://github.com.evil.example/foo.zip",
        "https://user:pass@github.com/foo.zip",
        "file:///etc/passwd",
    ],
)
def test_check_url_rejects(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        check_url(url, HOSTS)


def test_download_follows_allowed_redirect(tmp_path: Path, github: FakeGitHub) -> None:
    progress: list[tuple[int, int | None]] = []
    dest = tmp_path / "a.zip"
    digest = download_file(
        github.client(),
        SOURCE_URL,
        dest,
        allowed_hosts=HOSTS,
        max_bytes=10_000_000,
        expected_sha256=github.sha256,
        on_progress=lambda r, t: progress.append((r, t)),
    )
    assert digest == github.sha256
    assert dest.read_bytes() == github.archive
    assert github.requests == [SOURCE_URL, CODELOAD_URL]
    assert progress[-1][0] == len(github.archive)


def test_redirect_to_unlisted_host_is_refused(tmp_path: Path, github: FakeGitHub) -> None:
    github.add(SOURCE_URL, 302, b"", {"location": "https://evil.example/x.zip"})
    with pytest.raises(UnsafeUrlError):
        download_file(
            github.client(),
            SOURCE_URL,
            tmp_path / "a.zip",
            allowed_hosts=HOSTS,
            max_bytes=10_000_000,
        )
    assert "https://evil.example/x.zip" not in github.requests


def test_redirect_loop(tmp_path: Path, github: FakeGitHub) -> None:
    github.add(SOURCE_URL, 302, b"", {"location": SOURCE_URL})
    with pytest.raises(DownloadError, match="too many redirects"):
        download_file(
            github.client(),
            SOURCE_URL,
            tmp_path / "a.zip",
            allowed_hosts=HOSTS,
            max_bytes=10_000_000,
        )


@pytest.mark.parametrize("declare_length", [True, False])
def test_size_limit(tmp_path: Path, github: FakeGitHub, declare_length: bool) -> None:
    body = b"x" * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        if declare_length:
            return httpx.Response(200, content=body)
        return httpx.Response(200, content=iter([body[:2500], body[2500:]]))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(DownloadError, match="limit"):
        download_file(client, CODELOAD_URL, tmp_path / "a.zip", allowed_hosts=HOSTS, max_bytes=1000)
    assert list(tmp_path.iterdir()) == []


def test_checksum_mismatch_leaves_nothing(tmp_path: Path, github: FakeGitHub) -> None:
    with pytest.raises(ArchiveError, match="mismatch"):
        download_file(
            github.client(),
            SOURCE_URL,
            tmp_path / "a.zip",
            allowed_hosts=HOSTS,
            max_bytes=10_000_000,
            expected_sha256="0" * 64,
        )
    assert list(tmp_path.iterdir()) == []


def test_http_error_and_network_error(tmp_path: Path, github: FakeGitHub) -> None:
    with pytest.raises(DownloadError, match="HTTP 404"):
        download_file(
            github.client(),
            "https://github.com/missing.zip",
            tmp_path / "a.zip",
            allowed_hosts=HOSTS,
            max_bytes=1000,
        )
    github.fail_all = True
    with pytest.raises(DownloadError, match="could not reach"):
        download_file(
            github.client(), SOURCE_URL, tmp_path / "a.zip", allowed_hosts=HOSTS, max_bytes=1000
        )


def test_fetch_bytes_limit(github: FakeGitHub) -> None:
    github.add(CODELOAD_URL, 200, b"y" * 2000)
    status, body, _ = fetch_bytes(
        github.client(), CODELOAD_URL, allowed_hosts=HOSTS, max_bytes=5000
    )
    assert status == 200 and hashlib.sha256(body).hexdigest()
    with pytest.raises(DownloadError, match="exceeds"):
        fetch_bytes(github.client(), CODELOAD_URL, allowed_hosts=HOSTS, max_bytes=100)
