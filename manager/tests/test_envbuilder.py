from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path

import pytest

from safe_agents_manager.config import Settings
from safe_agents_manager.envbuilder import UvEnvBuilder
from safe_agents_manager.errors import InstallError


def fake_uv(tmp_path: Path, body: str) -> Path:
    """A stand-in for the uv executable: a Python script with a shebang."""
    script = tmp_path / "uv"
    script.write_text(f"#!{sys.executable}\nimport sys, time\n{body}\n")
    script.chmod(0o755)
    return script


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shebang script as uv")
def test_output_is_forwarded_while_uv_runs(tmp_path: Path, settings: Settings) -> None:
    uv = fake_uv(tmp_path, "print('Resolved 3 packages', flush=True)\ntime.sleep(1)\nprint('done')")
    builder = UvEnvBuilder(dataclasses.replace(settings, uv_path=uv))
    seen: list[tuple[float, str]] = []
    start = time.monotonic()
    builder._run(["pip"], lambda line: seen.append((time.monotonic() - start, line)))
    lines = [line for _, line in seen]
    assert lines[1:] == ["Resolved 3 packages", "done"]
    first_output_at = seen[1][0]
    assert first_output_at < 0.9, "lines arrive before uv finishes"


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shebang script as uv")
def test_failure_and_timeout_are_reported(tmp_path: Path, settings: Settings) -> None:
    failing = fake_uv(tmp_path, "print('error: no matching distribution')\nsys.exit(2)")
    builder = UvEnvBuilder(dataclasses.replace(settings, uv_path=failing))
    with pytest.raises(InstallError, match=r"(?s)exit 2.*no matching distribution"):
        builder._run(["pip"], lambda _: None)

    slow = fake_uv(tmp_path, "time.sleep(30)")
    builder = UvEnvBuilder(dataclasses.replace(settings, uv_path=slow), timeout_s=0.5)
    with pytest.raises(InstallError, match="timed out"):
        builder._run(["venv"], lambda _: None)


def test_missing_uv(tmp_path: Path, settings: Settings) -> None:
    builder = UvEnvBuilder(dataclasses.replace(settings, uv_path=tmp_path / "nope"))
    with pytest.raises(InstallError, match="uv was not found"):
        builder._run(["venv"], lambda _: None)


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shebang script as uv")
def test_timeout_stops_children_holding_the_output(tmp_path: Path, settings: Settings) -> None:
    # uv starts a child that inherits stdout and keeps running: the timeout must still win.
    uv = fake_uv(
        tmp_path,
        "import subprocess\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)",
    )
    builder = UvEnvBuilder(dataclasses.replace(settings, uv_path=uv), timeout_s=1)
    started = time.monotonic()
    with pytest.raises(InstallError, match="timed out"):
        builder._run(["pip"], lambda _: None)
    assert time.monotonic() - started < 10
