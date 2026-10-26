#!/usr/bin/env python3
"""Package built helpers and optionally a shared app's public Client ID."""

import argparse
import json
import shutil
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--shared-x-config", type=Path,
    help="Read only the public Client ID from this local config for a friend ZIP.",
)
args = parser.parse_args()
shared_client = None
if args.shared_x_config:
    shared_client = json.loads(args.shared_x_config.expanduser().read_text()).get("client_id")
    if not isinstance(shared_client, str) or not shared_client.strip():
        parser.error("The shared X configuration must contain a public client_id.")
    shared_client = shared_client.strip()

root = Path(__file__).resolve().parents[1]
release = root.parent / "release" / (
    "bookmark-library-friend" if shared_client else "bookmark-library-release"
)
dest = release / "plugins/bookmark-library"

required = [
    ".codex-plugin/plugin.json", ".mcp.json", "README.md", "THIRD_PARTY_NOTICES.md",
    "package.json", "package-lock.json", "requirements.txt", "tsconfig.json",
    "dist/panel.html", "dist/server.mjs", "bin/native-helper", "bin/TranscribeAudio",
    "vendor/transcribe/runtime/Package.swift", "vendor/transcribe/runtime/Package.resolved",
]
missing = [name for name in required if not (root / name).is_file()]
for name in ("LICENSE", "docs/SETUP_PROMPT.md", "docs/UPDATE_PROMPT.md"):
    if not (root.parent / name).is_file():
        missing.append("../" + name)
if missing:
    raise SystemExit("Missing release files; run npm run build and npm run build:native first: " + ", ".join(missing))

# An allowlist prevents credentials, library files and build caches from entering the ZIP.
files = {root / name for name in required}
for pattern in (
    "assets/*.png", "assets/*.svg", "licenses/*.txt", "python/*.py",
    "src/*.ts", "src/*.html", "scripts/*.py", "scripts/*.mjs", "scripts/*.sh",
    "scripts/*.swift", "skills/**/*.md", "vendor/transcribe/scripts/*.py",
    "vendor/transcribe/runtime/Sources/**/*.swift",
):
    files.update(root.glob(pattern))
if any(path.is_symlink() for path in files):
    raise SystemExit("Release inputs must be regular files, not symlinks.")

marketplace = json.loads((root.parent / ".agents/plugins/marketplace.json").read_text())
marketplace["plugins"][0]["source"]["path"] = "./plugins/bookmark-library"
if shared_client:
    marketplace["name"] = "bookmark-library-friend"
if release.exists():
    shutil.rmtree(release)
for path in sorted(files):
    target = dest / path.relative_to(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)

market = release / ".agents/plugins"
market.mkdir(parents=True, exist_ok=True)
(market / "marketplace.json").write_text(json.dumps(marketplace, indent=2) + "\n")
shutil.copy2(root.parent / "LICENSE", dest / "LICENSE")
if not shared_client:
    for name in ("SETUP_PROMPT.md", "UPDATE_PROMPT.md"):
        shutil.copy2(root.parent / "docs" / name, release / name)
if shared_client:
    (dest / "x-client.json").write_text(json.dumps({"client_id": shared_client}, indent=2) + "\n")
archive = shutil.make_archive(str(release), "zip", release.parent, release.name)
print(archive)
