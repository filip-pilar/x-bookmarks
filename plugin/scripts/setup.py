#!/usr/bin/env python3
"""Prepare a private Apple silicon library; no bookmark API requests or import."""

import argparse, fcntl, hashlib, json, os, platform, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--library", default="~/Library/Application Support/BookmarkLibrary")
p.add_argument("--client-id")
p.add_argument("--reuse-python")
p.add_argument("--reuse-model")
p.add_argument("--offline", action="store_true")
a = p.parse_args()
os.umask(0o077)
home = Path(a.library).expanduser().resolve()
if platform.system() != "Darwin" or platform.machine() != "arm64":
    raise SystemExit("Bookmark Library requires an Apple silicon Mac.")
if int(platform.mac_ver()[0].split(".")[0]) < 14:
    raise SystemExit("Bookmark Library requires macOS 14 or newer.")
home.mkdir(parents=True, exist_ok=True, mode=0o700)
lock = (home / "service.lock").open("a")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("Stop this library's Bookmark Library service before running setup. Preserve its queue and credentials.")
os.environ["BOOKMARK_LIBRARY_HOME"] = str(home)
sys.path.insert(0, str(ROOT / "python"))
from store import atomic, save_config, init, set_setting, VERSION
init()
set_setting("paused", True)
atomic(home / "preflight.json", {"ready": False, "version": VERSION, "checks": {}})
cfg_path = home / "config.json"
cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
client_id = a.client_id or cfg.get("client_id")
if not isinstance(client_id, str) or not client_id.strip():
    raise SystemExit("Provide your X app's public Client ID with --client-id.")
client_id = client_id.strip()
if cfg.get("account") and client_id != cfg.get("client_id"):
    raise SystemExit("Disconnect X before changing this library's Client ID, then reconnect after setup.")
cfg["client_id"] = client_id
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")


def run(args):
    subprocess.run([str(x) for x in args], check=True)


missing = [x for x in ("node", "ffmpeg", "yt-dlp") if not shutil.which(x)]
if missing:
    if a.offline:
        raise SystemExit("Offline setup cannot install missing dependencies: " + ", ".join(missing))
    if not shutil.which("brew"):
        raise SystemExit(
            "Install Homebrew, then rerun setup to install Node, FFmpeg and yt-dlp."
        )
    run(["brew", "install", *missing])
if int(subprocess.check_output(["node", "--version"], text=True).strip().lstrip("v").split(".")[0]) < 22:
    raise SystemExit("Node.js 22+ is required; upgrade Node and rerun setup.")
py = Path(a.reuse_python) if a.reuse_python else home / "venv/bin/python"
if not a.reuse_python:
    base = shutil.which("python3.14")
    if not base:
        if a.offline:
            raise SystemExit("Python 3.14 is missing; rerun setup online.")
        if not shutil.which("brew"):
            raise SystemExit("Python 3.14 is required; install it and rerun setup.")
        run(["brew", "install", "python@3.14"])
        base = "/opt/homebrew/bin/python3.14"
    if not py.exists():
        run([base, "-m", "venv", home / "venv"])
    run(
        [
            py,
            "-m",
            "pip",
            "install",
            *(["--no-index"] if a.offline else []),
            "--disable-pip-version-check",
            "--cache-dir",
            home / "cache/pip",
            "-r",
            ROOT / "requirements.txt",
        ]
    )
run([py, "-m", "pip", "check"])
run([sys.executable, ROOT / "scripts/build-native.py", "--output", home / "bin",
     "--build-dir", home / "cache/native-build", *(["--offline"] if a.offline else [])])
model = (
    Path(a.reuse_model).resolve() if a.reuse_model else home / "models/embeddinggemma-2"
)
revision = "914f7f89142e33e77833254d9c9b90c3cef7303b"
if not a.reuse_model and not a.offline:
    script = (
        'from huggingface_hub import snapshot_download; snapshot_download(repo_id="google/embeddinggemma-2",revision='
        + repr(revision)
        + ",local_dir="
        + repr(str(model))
        + ")"
    )
    run([py, "-c", script])
weights = model / "model.safetensors"
if not weights.is_file():
    raise SystemExit("Gemma model missing. Rerun setup online to download it on this Mac.")
hash = hashlib.sha256()
with weights.open("rb") as f:
    for b in iter(lambda: f.read(1024 * 1024), b""):
        hash.update(b)
if (
    hash.hexdigest()
    != "197a32965d4b1105faf060417baa899e193fb73cd401f42ec9295234d5553d79"
):
    raise SystemExit(
        "Model checksum mismatch; expected the verified Google checkpoint."
    )
if a.offline:
    cfg["offline"] = True
else:
    cfg.pop("offline", None)
save_config(cfg)
atomic(
    home / "runtime.json",
    {"python": str(py.absolute()), "model": str(model), "revision": revision},
)
run([py, ROOT / "scripts/preflight.py"])
set_setting("pause_reason", "")
print(json.dumps({"ready": True, "library": str(home), "paused": True,
                  "import_started": False, "preflight": str(home / "preflight.json")}))
