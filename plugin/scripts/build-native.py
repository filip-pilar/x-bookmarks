#!/usr/bin/env python3
"""Build the Apple silicon helpers without starting a library or loading models."""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "bin")
    parser.add_argument("--build-dir", type=Path, default=ROOT / ".build/native")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("Native helpers require an Apple silicon Mac.")
    for tool in ("swift", "swiftc"):
        if not shutil.which(tool):
            raise SystemExit("Install Swift 6.2+ with Xcode or compatible Command Line Tools.")
    swift = subprocess.check_output(["swift", "--version"], text=True)
    version = re.search(r"Swift version (\d+)\.(\d+)", swift)
    if not version or tuple(map(int, version.groups())) < (6, 2):
        raise SystemExit("Swift 6.2+ is required. Install/select Xcode or an official Swift.org toolchain, then rerun setup.")
    sources = [ROOT / "scripts/native.swift", ROOT / "vendor/transcribe/runtime/Package.resolved",
               *sorted((ROOT / "vendor/transcribe/runtime").rglob("*.swift"))]
    identity = hashlib.sha256((swift + "".join(hashlib.sha256(p.read_bytes()).hexdigest() for p in sources)).encode()).hexdigest()
    output = args.output.expanduser().resolve()
    stamp = output / "native-build.json"
    names = ("native-helper", "TranscribeAudio")
    if stamp.exists() and all((output / name).is_file() for name in names):
        saved = json.loads(stamp.read_text())
        if saved.get("source") == identity and saved.get("binaries") == {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in names
        }:
            print(f"Local native helpers already match source: {output}")
            return
    if args.offline:
        raise SystemExit("Offline setup requires previously built helpers matching this source and toolchain. Rerun setup online to build them.")
    build = args.build_dir.expanduser().resolve()
    modules = build / "modules"
    modules.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(modules), SWIFT_MODULECACHE_PATH=str(modules))
    subprocess.run(
        ["swiftc", "-O", "-target", "arm64-apple-macosx14.0", "-module-cache-path", str(modules),
         str(ROOT / "scripts/native.swift"), "-o", str(output / "native-helper")],
        env=env, check=True,
    )
    # Keep SwiftPM's generated metadata and dependency checkouts out of the vendored source.
    package = build / "package"
    shutil.copytree(ROOT / "vendor/transcribe/runtime", package, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".build", ".swiftpm"))
    command = [
        "swift", "build", "-c", "release", "--package-path", str(package),
        "--scratch-path", str(build / "swift"), "--cache-path", str(build / "cache"),
        "--config-path", str(build / "config"), "--security-path", str(build / "security"),
        "--manifest-cache", "local", "--force-resolved-versions", "--product", "TranscribeAudio",
    ]
    subprocess.run(command, env=env, check=True)
    binary_dir = Path(subprocess.check_output(command + ["--show-bin-path"], env=env, text=True).strip())
    shutil.copy2(binary_dir / "TranscribeAudio", output / "TranscribeAudio")
    stamp.write_text(json.dumps({"source": identity, "binaries": {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in names
    }}, indent=2) + "\n")
    print(f"Native helpers built in {output}")


if __name__ == "__main__":
    main()
