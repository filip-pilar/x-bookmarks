#!/usr/bin/env python3
"""Update the version fields consumed by the package, MCP server and panel."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("version", help="New major.minor.patch version")
args = parser.parse_args()
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
    parser.error("Use a major.minor.patch version.")
plugin = ROOT / "plugin"
old = json.loads((plugin / "package.json").read_text())["version"]
if tuple(map(int, args.version.split("."))) <= tuple(map(int, old.split("."))):
    parser.error("The new version must be greater than " + old)
updates = {}
for name in ("package.json", "plugin.json", ".codex-plugin/plugin.json", "package-lock.json"):
    path = plugin / name
    data = json.loads(path.read_text())
    if data["version"] != old:
        raise SystemExit("Version mismatch in " + name)
    data["version"] = args.version
    if name == "package-lock.json":
        if data["packages"][""]["version"] != old:
            raise SystemExit("Version mismatch in npm root package")
        data["packages"][""]["version"] = args.version
    updates[path] = json.dumps(data, indent=2) + "\n"
for name in ("src/server.ts", "src/panel.ts"):
    path = plugin / name
    text = path.read_text()
    if old not in text:
        raise SystemExit("Expected version not found in " + name)
    updates[path] = text.replace(old, args.version)
for path, text in updates.items():
    path.write_text(text)
print(old + " -> " + args.version + "; review the diff before committing and tagging.")
