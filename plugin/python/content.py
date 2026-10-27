import hashlib, io, ipaddress, json, os, socket, subprocess, sys, time, shutil, tempfile
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from urllib.request import Request, build_opener, HTTPRedirectHandler
from store import ROOT, HOME, atomic, config, fingerprint
from sources import (
    POLICY,
    cached_json,
    checksum,
    reusable,
    reserve_space,
    RESERVE,
    DiskSpaceError,
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def public_url(url):
    p = urlsplit(url)
    if (
        p.scheme not in ("http", "https")
        or not p.hostname
        or p.username
        or p.password
        or p.port not in (None, 80, 443)
    ):
        raise ValueError("Unsupported source URL.")
    addresses = socket.getaddrinfo(
        p.hostname, p.port or (443 if p.scheme == "https" else 80)
    )
    if not addresses or any(
        not ipaddress.ip_address(a[4][0]).is_global for a in addresses
    ):
        raise ValueError("Local and private network URLs are not source content.")
    return url


def command(args, timeout=1800, env=None):
    p = subprocess.run(
        [str(a) for a in args], capture_output=True, text=True, timeout=timeout, env=env
    )
    if p.returncode:
        from diagnostics import safe_error
        raise RuntimeError(
            f"{Path(str(args[0])).name} failed (exit {p.returncode}): {safe_error(p.stderr or p.stdout)}"
        )
    return p.stdout


def ocr(path):
    result = subprocess.run(
        [str(HOME / "bin/native-helper")],
        input=json.dumps({"op": "ocr", "path": str(path)}),
        text=True,
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        from diagnostics import safe_error
        raise RuntimeError("macOS Vision helper failed: " + safe_error(result.stderr))
    return "\n".join(json.loads(result.stdout))


def audio_file(path, content_hash, duration):
    import soundfile as sf

    wav = path.with_suffix(".wav")
    stamp = wav.with_suffix(".wav.source.json")
    record = cached_json(stamp)
    if (
        wav.exists()
        and record
        and record.get("sha256") == content_hash
        and record.get("decoder") == "timeline-v1"
    ):
        try:
            info = sf.info(wav)
            if (
                info.samplerate == 16000
                and info.channels == 1
                and abs(info.duration - duration) < 0.5
            ):
                return wav
        except (RuntimeError, OSError):
            pass
    reserve_space(int(duration * 16000 * 2) + 1024**2)
    temporary = wav.with_suffix(".building.wav")
    command(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-y",
            "-i",
            path,
            "-vn",
            "-af",
            "aresample=async=1:first_pts=0,apad",
            "-t",
            str(duration),
            "-ac",
            "1",
            "-ar",
            "16000",
            temporary,
        ]
    )
    info = sf.info(temporary)
    if abs(info.duration - duration) >= 0.5:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Extracted audio is incomplete.")
    os.replace(temporary, wav)
    atomic(stamp, {"sha256": content_hash, "decoder": "timeline-v1"})
    return wav


def transcribe(path, directory, *, rerun=False):
    runner = ROOT / "vendor/transcribe/scripts/transcribe.py"
    result = json.loads(
        command(
            [
                sys.executable,
                runner,
                path,
                "--destination",
                directory / "transcriptions",
            ] + (["--rerun"] if rerun else []),
            env=dict(os.environ, BOOKMARK_LIBRARY_OFFLINE="1" if config().get("offline") else "0"),
        )
    )
    raw = json.loads((Path(result["directory"]) / "recognition.json").read_text())
    return raw, result


def probe(path):
    return json.loads(
        command(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                path,
            ],
            60,
        )
    )
