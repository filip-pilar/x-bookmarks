#!/usr/bin/env python3
"""Build the Apple silicon helpers without starting a library or loading models."""

import os
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("Native helpers require an Apple silicon Mac.")
    for tool in ("swift", "swiftc"):
        if not shutil.which(tool):
            raise SystemExit("Install Swift 6.2+ with Xcode or compatible Command Line Tools.")

    build = ROOT / ".build/native"
    modules = build / "modules"
    modules.mkdir(parents=True, exist_ok=True)
    output = ROOT / "bin"
    output.mkdir(exist_ok=True)
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
    print(f"Native helpers built in {output}")


if __name__ == "__main__":
    main()
