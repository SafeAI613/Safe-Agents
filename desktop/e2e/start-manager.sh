#!/usr/bin/env bash
# Starts a real manager for the E2E tests: fresh data folder, the example agent installed
# from a zip, a local catalog file, in-memory secrets and a fake OpenRouter.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
work="$here/.tmp"
rm -rf "$work"
mkdir -p "$work/exports"

python3 - "$repo/examples/foo-agent" "$work/foo-agent.zip" <<'PY'
import sys, zipfile
from pathlib import Path
src, dest = Path(sys.argv[1]), sys.argv[2]
with zipfile.ZipFile(dest, "w") as zf:
    for path in src.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            zf.write(path, Path("foo-agent") / path.relative_to(src))
PY

cat > "$work/registry.json" <<'JSON'
{
  "registry_version": 1,
  "agents": [
    {
      "id": "foo_agent",
      "name": "Foo Agent",
      "description": "Minimal reference agent.",
      "latest": "2.0.0",
      "versions": [
        {
          "version": "2.0.0",
          "manifest_url": "https://raw.githubusercontent.com/foo/foo-agent/v2.0.0/manifest.json",
          "source_url": "https://github.com/foo/foo-agent/releases/download/v2.0.0/foo.zip",
          "sha256": "1111111111111111111111111111111111111111111111111111111111111111"
        }
      ]
    },
    {
      "id": "bar_agent",
      "name": "Bar Agent",
      "description": "Lorem ipsum dolor sit amet.",
      "creator_name": "bar",
      "latest": "2.0.0",
      "versions": [
        {
          "version": "2.0.0",
          "manifest_url": "https://raw.githubusercontent.com/bar/bar-agent/v2.0.0/manifest.json",
          "source_url": "https://github.com/bar/bar-agent/releases/download/v2.0.0/bar.zip",
          "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
        }
      ]
    }
  ]
}
JSON

manager=(uv run --quiet --project "$repo/manager" safe-agents-manager --home "$work/home")
"${manager[@]}" install "$work/foo-agent.zip"
# A second agent, made with the developer tools, for the "install from ZIP" test.
"${manager[@]}" new-agent "$work/zip-agent" --id zip_agent --name "Zip Agent" > /dev/null
"${manager[@]}" package "$work/zip-agent" -o "$work/zip-agent.zip" > /dev/null

export SAFE_AGENTS_SECRETS=memory
export SAFE_AGENTS_TOKEN=e2e-token
export SAFE_AGENTS_CORS_ORIGINS=http://localhost:1420
export SAFE_AGENTS_OPENROUTER_URL=http://127.0.0.1:23710/api/v1
export SAFE_AGENTS_REGISTRY_URL="$work/registry.json"
export SAFE_AGENTS_EXPORT_DIR="$work/exports"
export FOO_AGENT_FAKE_LLM=1
unset OPENROUTER_API_KEY
exec "${manager[@]}" serve --port 23700
