#!/usr/bin/env python3
"""Check public release metadata and, optionally, the built installable archive."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import stat
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path)
parser.add_argument("--tag")
args = parser.parse_args()
plugin = ROOT / "plugin"
version = json.loads((plugin / "package.json").read_text())["version"]
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
    raise SystemExit("Invalid plugin version")
for name in ("plugin.json", ".codex-plugin/plugin.json", "package-lock.json"):
    data = json.loads((plugin / name).read_text())
    if data["version"] != version:
        raise SystemExit("Version mismatch: " + name)
    if name == "package-lock.json" and data["packages"][""]["version"] != version:
        raise SystemExit("npm root package version mismatch")
for name in ("src/server.ts", "src/panel.ts"):
    source = (plugin / name).read_text()
    versions = re.findall(r'(?:version: |backend_version !== |control-)["\']?([0-9]+\.[0-9]+\.[0-9]+)', source)
    if not versions or set(versions) != {version}:
        raise SystemExit("MCP/UI version mismatch: " + name)
if args.tag and args.tag != "v" + version:
    raise SystemExit("Tag must match plugin version: v" + version)
if not (ROOT / "LICENSE").read_text().startswith("MIT License"):
    raise SystemExit("MIT license is missing")

if args.archive:
    base = "bookmark-library-release/"
    prefix = base + "plugins/bookmark-library/"
    required = {base + ".agents/plugins/marketplace.json", base + "SETUP_PROMPT.md", base + "UPDATE_PROMPT.md", base + "KNOWN_ISSUES.md", base + "ARCHITECTURE.md"}
    required.update(prefix + name for name in (
        "LICENSE", ".codex-plugin/plugin.json", ".mcp.json", "package.json",
        "dist/server.mjs", "dist/panel.html", "scripts/build-native.py", "scripts/native.swift", "scripts/preflight.py",
        "vendor/transcribe/runtime/Package.swift", "vendor/transcribe/runtime/Package.resolved",
        "vendor/transcribe/runtime/Sources/TranscribeAudio/main.swift",
        "scripts/setup.py", "scripts/verify-description.py", "scripts/diagnostics.py", "scripts/launch.sh", "skills/setup/SKILL.md",
    ))
    forbidden_names = {"x-client.json", "config.json", "runtime.json", "service.json", ".env"}
    forbidden_dirs = {"node_modules", ".git", ".venv", ".build", "models", "items", "sources", "scans", "bin", "private", "reports", "cache", "backups"}
    with ZipFile(args.archive) as archive:
        if archive.testzip():
            raise SystemExit("ZIP CRC validation failed")
        names = set(archive.namelist())
        if required - names:
            raise SystemExit("Missing archive files: " + str(sorted(required - names)))
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in entry.filename or not entry.filename.startswith(base):
                raise SystemExit("Unsafe archive path: " + entry.filename)
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise SystemExit("Symlink in release: " + entry.filename)
            if path.name in forbidden_names or path.name.startswith(".env.") or path.suffix in {".db", ".sqlite", ".log", ".pyc"} or forbidden_dirs.intersection(path.parts):
                raise SystemExit("Private/cache file in public release: " + entry.filename)
        for name in ("scripts/launch.sh",):
            if not (archive.getinfo(prefix + name).external_attr >> 16) & 0o111:
                raise SystemExit("Missing executable permission: " + name)
        manifest = json.loads(archive.read(prefix + ".codex-plugin/plugin.json"))
        if manifest["version"] != version:
            raise SystemExit("Archive version mismatch")
        market = json.loads(archive.read(base + ".agents/plugins/marketplace.json"))
        if market["name"] != "bookmark-library-local" or market["plugins"][0]["source"]["path"] != "./plugins/bookmark-library":
            raise SystemExit("Unexpected marketplace identity/path")
        if archive.read(prefix + "LICENSE") != (ROOT / "LICENSE").read_bytes():
            raise SystemExit("Archive license mismatch")
    print("Public installable archive verified: " + str(args.archive))
print("Source metadata verified: " + version)
