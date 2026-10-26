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


def valid_image(data):
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:
        return False


def fetch(url, dest, limit=24 * 1024 * 1024, *, image=False):
    dest = Path(dest)
    stamp = dest.with_suffix(dest.suffix + ".source.json")
    record = cached_json(stamp)
    mode, generation = POLICY.get()
    if dest.exists() and dest.stat().st_size and reusable(record) and (not image or valid_image(dest.read_bytes())):
        if not record or (
            record.get("url") == url and record.get("sha256") == checksum(dest)
        ):
            if not record:
                atomic(stamp, {"url": url, "sha256": checksum(dest), "generation": ""})
            return dest
    shared = HOME / "sources/http" / fingerprint(url)
    shared_record = cached_json(shared.with_suffix(".json"))
    if (
        shared.exists()
        and shared_record
        and reusable(shared_record)
        and checksum(shared) == shared_record.get("sha256")
        and (not image or valid_image(shared.read_bytes()))
    ):
        atomic(dest, shared.read_bytes())
        atomic(stamp, shared_record)
        return dest
    if mode == "rebuild" or config().get("offline"):
        raise RuntimeError("Saved source missing or corrupt; use Repair to fetch it.")
    from urllib.error import HTTPError

    original = url
    opener = build_opener(NoRedirect)
    for _ in range(8):
        public_url(url)
        try:
            response = opener.open(
                Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 BookmarkLibrary/0.3",
                        "Accept": "*/*",
                    },
                ),
                timeout=40,
            )
        except HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                url = urljoin(url, e.headers["Location"])
                continue
            raise RuntimeError(f"Source returned HTTP {e.code}.") from None
        with response:
            reserve_space(limit)
            data = response.read(limit + 1)
            content_type = response.headers.get("Content-Type", "")
        if len(data) > limit:
            raise RuntimeError(
                "Source exceeds the 24 MB document/image extraction limit."
            )
        if image and not valid_image(data):
            raise RuntimeError("Source did not return a decodable image; invalid bytes were not cached.")
        record = {
            "url": original,
            "final_url": url,
            "content_type": content_type,
            "sha256": hashlib.sha256(data).hexdigest(),
            "generation": generation,
        }
        atomic(shared, data)
        atomic(shared.with_suffix(".json"), record)
        atomic(dest, data)
        atomic(stamp, record)
        return dest
    raise RuntimeError("Too many source redirects.")


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


def external_document(url, directory):
    from bs4 import BeautifulSoup
    from markdownify import markdownify
    import trafilatura

    key = hashlib.sha256(url.encode()).hexdigest()[:20]
    dest = directory / "links" / key
    dest.mkdir(parents=True, exist_ok=True)
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    if urlsplit(url).path.lower().endswith(".pdf") or host in (
        "youtube.com",
        "youtu.be",
        "vimeo.com",
        "tiktok.com",
    ):
        raise RuntimeError(
            "Unsupported linked PDF or video platform; original link retained."
        )
    source = fetch(url, dest / "source.html")
    record = cached_json(source.with_suffix(".html.source.json")) or {}
    if "pdf" in record.get("content_type", "") or record.get(
        "content_type", ""
    ).startswith(("video/", "audio/")):
        raise RuntimeError(
            "Unsupported linked PDF or standalone media; original link retained."
        )
    html = source.read_text(errors="replace")
    base_url = record.get("final_url", url)
    soup = BeautifulSoup(html, "html.parser")
    description = soup.find("meta", attrs={"name": "description"})
    page_metadata = (
        soup.title.get_text(strip=True) + "\n\n" + description["content"]
        if soup.title and description and description.get("content")
        else ""
    )
    for x in soup(["script", "style", "nav", "header", "footer", "form", "iframe"]):
        x.decompose()
    main = (
        soup.select_one("article.markdown-body")
        or soup.select_one("article")
        or soup.select_one("main")
    )
    warnings = []
    images = []
    if main is None:
        body = trafilatura.extract(
            html,
            include_links=True,
            include_tables=True,
            include_images=True,
            output_format="xml",
        )
        if not body:
            if not page_metadata:
                raise RuntimeError(
                    "No readable article found; source retained for retry."
                )
            main = BeautifulSoup("<main></main>", "html.parser")
        else:
            main = BeautifulSoup(body, "xml")
            warnings.append(
                "Readability extraction may omit page layout or unsupported content."
            )
    for tag in main.find_all(["img", "graphic"]):
        src = tag.get("data-src") or tag.get("data-original") or tag.get("src")
        if (not src or src.startswith("data:")) and tag.get("srcset"):
            src = tag["srcset"].split(",")[-1].strip().split()[0]
        alt = tag.get("alt", "")
        if not src:
            continue
        if src.startswith("data:") and not alt and tag.get("aria-hidden") == "true":
            tag.decompose()
            continue
        img_url = urljoin(base_url, src)
        p = dest / (hashlib.sha256(img_url.encode()).hexdigest()[:20] + ".image")
        try:
            fetch(img_url, p, image=True)
            images.append({"path": str(p), "url": img_url, "alt": alt})
            tag.name = "img"
            tag.attrs = {"src": str(p), "alt": alt}
        except DiskSpaceError:
            raise
        except Exception as e:
            warnings.append("Linked image: " + str(e))
    for a in main.find_all("a", href=True):
        a["href"] = urljoin(base_url, a["href"])
    text = markdownify(str(main), heading_style="ATX")
    if not text.strip():
        if page_metadata:
            text = page_metadata
            warnings.append(
                "Interactive page: saved page metadata; live app state was not captured."
            )
        else:
            raise RuntimeError("Extracted document is empty.")
    atomic(dest / "article.md", f"# Linked source\n\nSource: {url}\n\n{text}")
    return {
        "text": text,
        "path": str(dest / "article.md"),
        "url": url,
        "images": images,
        "warnings": warnings,
    }


def media_file(media, directory):
    key = media["media_key"]
    if not __import__("re").fullmatch(r"[0-9]+_[0-9]+", key):
        raise ValueError("Invalid media identifier.")
    path = directory / "assets" / key
    path.parent.mkdir(parents=True, exist_ok=True)
    if media["type"] == "photo":
        return fetch(media["url"], path.with_suffix(".image"), image=True)
    path = path.with_suffix(".mp4")
    stamp = path.with_suffix(".source.json")
    record = cached_json(stamp)
    identity = fingerprint(media)
    mode, generation = POLICY.get()
    if (
        path.exists()
        and reusable(record)
        and (mode == "rebuild" or not record or record.get("fingerprint") == identity)
    ):
        try:
            if float(probe(path).get("format", {}).get("duration", 0)) > 0 and (
                not record
                or not record.get("sha256")
                or checksum(path) == record["sha256"]
            ):
                atomic(
                    stamp,
                    {
                        "fingerprint": identity,
                        "sha256": checksum(path),
                        "generation": (record or {}).get("generation", ""),
                    },
                )
                return path
        except RuntimeError:
            pass
    shared = HOME / "sources/media" / (identity + ".mp4")
    shared_record = cached_json(shared.with_suffix(".json"))
    if (
        shared.exists()
        and shared_record
        and reusable(shared_record)
        and checksum(shared) == shared_record.get("sha256")
    ):
        reserve_space(shared.stat().st_size)
        temporary = path.with_suffix(".copy.mp4")
        shutil.copyfile(shared, temporary)
        os.replace(temporary, path)
        atomic(stamp, shared_record)
        return path
    if mode == "rebuild" or config().get("offline"):
        raise RuntimeError(
            "Saved source missing or corrupt: video. Use Repair to fetch it."
        )
    variants = [
        v for v in media.get("variants", []) if v.get("content_type") == "video/mp4"
    ]
    if not variants:
        raise RuntimeError("X did not return an accessible original MP4 variant.")
    url = max(variants, key=lambda v: v.get("bit_rate", 0)).get("url")
    public_url(url)
    reserve_space()
    allowance = min(8 * 1024**3, max(0, (shutil.disk_usage(HOME).free - RESERVE) // 2))
    temporary = path.with_suffix(".download.mp4")
    pending_stamp = path.with_suffix(".download.json")
    pending = cached_json(pending_stamp)
    if pending and pending.get("fingerprint") != identity:
        for partial in (
            temporary,
            Path(str(temporary) + ".part"),
            Path(str(temporary) + ".ytdl"),
        ):
            partial.unlink(missing_ok=True)
    atomic(pending_stamp, {"fingerprint": identity})
    args = [
        "yt-dlp",
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--no-cache-dir",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--max-filesize",
        str(allowance),
        "-o",
        temporary,
        url,
    ]
    with tempfile.TemporaryFile() as output:
        child = subprocess.Popen([str(a) for a in args], stdout=output, stderr=output)
        began = time.monotonic()
        try:
            while child.poll() is None:
                reserve_space()
                if time.monotonic() - began > 1800:
                    raise RuntimeError(
                        "Video download timed out; partial download retained for resume."
                    )
                time.sleep(0.25)
            if child.returncode:
                from diagnostics import safe_error
                output.seek(max(0, output.tell() - 4000))
                raise RuntimeError(
                    "Video download failed; partial download retained for resume. "
                    + safe_error(output.read().decode(errors="replace"))
                )
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    if not temporary.exists():
        raise RuntimeError(
            "Video exceeds the available disk allowance or 8 GB extraction limit."
        )
    if float(probe(temporary).get("format", {}).get("duration", 0)) <= 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Downloaded video is incomplete.")
    os.replace(temporary, path)
    record = {
        "fingerprint": identity,
        "sha256": checksum(path),
        "generation": generation,
    }
    atomic(stamp, record)
    shared.parent.mkdir(parents=True, exist_ok=True)
    temporary = shared.with_suffix(".copy.mp4")
    reserve_space(path.stat().st_size)
    shutil.copyfile(path, temporary)
    os.replace(temporary, shared)
    atomic(shared.with_suffix(".json"), record)
    return path


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
