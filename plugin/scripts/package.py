#!/usr/bin/env python3
"""Package JavaScript bundles and native source; recipients build their own helpers."""

import hashlib
import json
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
release = root.parent / "release" / "bookmark-library-release"
dest = release / "plugins/bookmark-library"

required = [
    ".codex-plugin/plugin.json", ".mcp.json", "README.md", "THIRD_PARTY_NOTICES.md",
    "package.json", "package-lock.json", "requirements.txt", "tsconfig.json",
    "dist/panel.html", "dist/server.mjs", "scripts/build-native.py", "scripts/native.swift",
    "scripts/preflight.py",
    "vendor/transcribe/runtime/Package.swift", "vendor/transcribe/runtime/Package.resolved",
]
missing = [name for name in required if not (root / name).is_file()]
for name in ("LICENSE", "docs/SETUP_PROMPT.md", "docs/UPDATE_PROMPT.md", "docs/KNOWN_ISSUES.md", "docs/ARCHITECTURE.md"):
    if not (root.parent / name).is_file():
        missing.append("../" + name)
if missing:
    raise SystemExit("Missing release files; run npm run build first: " + ", ".join(missing))

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
for name in ("SETUP_PROMPT.md", "UPDATE_PROMPT.md", "KNOWN_ISSUES.md", "ARCHITECTURE.md"):
    shutil.copy2(root.parent / "docs" / name, release / name)
archive = shutil.make_archive(str(release), "zip", release.parent, release.name)
digest = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
(release.parent / "SHA256SUMS").write_text(f"{digest}  {Path(archive).name}\n")
print(archive)
