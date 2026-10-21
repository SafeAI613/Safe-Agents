"""Build the files Tauri bundles next to the app (``bundle.externalBin``):

* ``safe-agents-manager-<target triple>[.exe]`` – the manager as one executable (PyInstaller);
* ``uv-<target triple>[.exe]`` – a pinned uv release, verified against its published sha256.

Tauri strips the triple when bundling, so at runtime both sit next to each other and the
manager finds ``uv`` beside its own executable (see ``config.find_uv``).

Usage (from the repository root):
    uv run --project manager python manager/scripts/build_sidecar.py [--target TRIPLE] [--skip-uv]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

UV_VERSION = "0.11.32"
PYINSTALLER = "pyinstaller==6.22.3"
MANAGER_DIR = Path(__file__).resolve().parents[1]
BINARIES_DIR = MANAGER_DIR.parent / "desktop" / "src-tauri" / "binaries"


def host_triple() -> str:
    output = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout
    for line in output.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit("could not read the host target triple from `rustc -vV`")


def exe_suffix(triple: str) -> str:
    return ".exe" if "windows" in triple else ""


def build_manager(triple: str) -> Path:
    with tempfile.TemporaryDirectory() as work:
        subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(MANAGER_DIR),
                "--with",
                PYINSTALLER,
                "pyinstaller",
                "--onefile",
                "--noconfirm",
                "--name",
                "safe-agents-manager",
                "--distpath",
                str(Path(work) / "dist"),
                "--workpath",
                str(Path(work) / "build"),
                "--specpath",
                work,
                # Loaded dynamically, so PyInstaller cannot see them by itself.
                "--collect-data",
                "safe_agents_manager",
                "--collect-submodules",
                "uvicorn",
                "--collect-submodules",
                "keyring",
                "--copy-metadata",
                "keyring",
                str(MANAGER_DIR / "scripts" / "sidecar_entry.py"),
            ],
            check=True,
        )
        built = Path(work) / "dist" / f"safe-agents-manager{exe_suffix(triple)}"
        target = BINARIES_DIR / f"safe-agents-manager-{triple}{exe_suffix(triple)}"
        BINARIES_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, target)
        return target


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 (fixed https URL)
        data: bytes = response.read()
        return data


def fetch_uv(triple: str) -> Path:
    archive_name = f"uv-{triple}.zip" if "windows" in triple else f"uv-{triple}.tar.gz"
    base = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{archive_name}"
    archive = _download(base)
    expected = _download(f"{base}.sha256").decode().split()[0].lower()
    actual = hashlib.sha256(archive).hexdigest()
    if actual != expected:
        raise SystemExit(f"uv archive checksum mismatch: expected {expected}, got {actual}")

    name = f"uv{exe_suffix(triple)}"
    if archive_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            member = next(m for m in zf.namelist() if Path(m).name == name)
            content = zf.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
            member = next(m for m in tf.getmembers() if Path(m.name).name == name)
            extracted = tf.extractfile(member)
            if extracted is None:
                raise SystemExit("uv binary missing from the archive")
            content = extracted.read()
    target = BINARIES_DIR / f"uv-{triple}{exe_suffix(triple)}"
    BINARIES_DIR.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    target.chmod(0o755)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", help="Rust target triple (default: this machine's)")
    parser.add_argument("--skip-uv", action="store_true", help="do not download uv")
    args = parser.parse_args()
    triple = args.target or host_triple()
    print(f"building sidecars for {triple}", flush=True)
    print(f"manager -> {build_manager(triple)}", flush=True)
    if not args.skip_uv:
        print(f"uv -> {fetch_uv(triple)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
