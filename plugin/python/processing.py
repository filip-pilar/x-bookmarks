import json, time, io
from pathlib import Path
from store import (
    HOME,
    db,
    atomic,
    fingerprint,
    config,
    setting,
    set_setting,
    PROCESSOR_VERSION,
    upsert_page,
)
from content import media_file, external_document, transcribe, probe, ocr, audio_file
from embedding import encode, FINGERPRINT
from xapi import get, FIELDS
from sources import policy, reserve_space, DiskSpaceError, context_request
from context import capture, links


def segments(text, size=1800, overlap=200):
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind("\n", start + size // 2, end)
            if cut > start:
                end = cut
        yield start, end, text[start:end]
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


class Paused(Exception):
    pass


def process(item, retry=False, mode="repair", generation=""):
    if config().get("offline"):
        mode = "rebuild"
    with policy(mode, generation):
        if mode == "refresh":
            if setting("paused", False):
                raise Paused()
            reserve_space()
            payload = context_request("/tweets/" + item, FIELDS, get)
            post = payload.get("data")
            if (
                not isinstance(post, dict)
                or post.get("id") != item
                or payload.get("errors")
            ):
                raise RuntimeError(
                    "Incomplete bookmark refresh; saved source retained."
                )
            with db() as c:
                upsert_page(c, {**payload, "data": [post]})
        return _process(item, retry)


def _process(item, retry=False):
    import numpy as np
    from PIL import Image
    import cv2
    import soundfile as sf

    with db() as c:
        row = dict(c.execute("SELECT * FROM items WHERE id=?", (item,)).fetchone())
    directory = HOME / "items" / item
    directory.mkdir(parents=True, exist_ok=True)
    reserve_space()
    raw = json.loads(row["raw"])
    post = raw["post"]
    warnings = []
    entries = []
    entry_ids = set()
    paragraphs = []
    atomic(directory / "source.json", raw)
    model_cache = directory / "vector-cache"
    model_cache.mkdir(exist_ok=True)

    def progress(stage, detail=""):
        if setting("paused", False):
            raise Paused()
        set_setting("processing", {"item": item, "stage": stage, "detail": detail})

    def add_text(text, kind="text", meta=None):
        for start, end, part in segments(text):
            add(
                kind,
                part,
                {**(meta or {}), "start_char": start, "end_char": end},
                text="title: " + row["title"] + " | text: " + part,
            )

    def add(kind, body="", meta=None, **inputs):
        progress(
            "indexing",
            {"video": "Video moments", "audio": "Audio", "image": "Images"}.get(
                kind, "Text and context"
            ),
        )
        meta = meta or {}
        key = fingerprint([FINGERPRINT, kind, body, meta, inputs.get("text")])
        if key in entry_ids:
            return
        path = model_cache / (key + ".npy")
        vec = None
        if path.exists():
            try:
                candidate = np.load(path, allow_pickle=False)
                if (
                    candidate.shape == (768,)
                    and np.isfinite(candidate).all()
                    and abs(float(np.linalg.norm(candidate)) - 1) < 0.001
                ):
                    vec = candidate.astype(np.float32)
            except (OSError, ValueError, EOFError):
                pass
        if vec is None:
            vec = encode(**inputs)[0]
            output = io.BytesIO()
            np.save(output, vec, allow_pickle=False)
            atomic(path, output.getvalue())
        entries.append(
            {
                "id": item + ":" + key,
                "item": item,
                "kind": kind,
                "text": body,
                "metadata": meta,
                "vector": vec.tobytes(),
                "model": FINGERPRINT,
            }
        )
        entry_ids.add(key)

    text = post.get("note_tweet", {}).get("text") or post.get("text", "")
    article = post.get("article", {})
    if article:
        text = article.get("plain_text") or text
        warnings.append(
            "X Article images retained as a gallery; inline positions were not supplied by X."
        )
        if not article.get("plain_text"):
            warnings.append("Full X Article body not supplied.")
    paragraphs.append(
        f"# {row['title']}\n\nSource: {row['url']}\nAuthor: @{row['author']}\n\n{text}"
    )
    add_text(text)
    context, context_media, context_warnings = capture(
        post, directory, get, progress, retry, row["hash"]
    )
    warnings.extend(context_warnings)
    for p in sorted(context, key=lambda p: int(p["id"])):
        body = p.get("article", {}).get("plain_text") or p.get("note_tweet", {}).get(
            "text", p.get("text", "")
        )
        url = f"https://x.com/i/status/{p['id']}"
        paragraphs.append(f"## Context\n\nSource: {url}\n\n{body}")
        add_text(body, "context", {"url": url, "source_id": p["id"]})

    images = []
    keys = set(post.get("attachments", {}).get("media_keys", []))
    # The Article response exposes expanded media, with no placement information.
    if article:
        keys.update(article.get("media_entities", []))
        if article.get("cover_media"):
            keys.add(article["cover_media"])
    for related in context:
        keys.update(related.get("attachments", {}).get("media_keys", []))
        keys.update(related.get("article", {}).get("media_entities", []))
        if related.get("article", {}).get("cover_media"):
            keys.add(related["article"]["cover_media"])
    all_media = {
        m["media_key"]: m
        for m in raw.get("includes", {}).get("media", []) + context_media
    }
    for media in all_media.values():
        if media["media_key"] not in keys:
            continue
        try:
            progress("downloading", "Images and videos")
            path = media_file(media, directory)
            hasher = __import__("hashlib").sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    hasher.update(block)
            content_hash = hasher.hexdigest()
            if media["type"] == "photo":
                images.append(
                    {
                        "path": str(path),
                        "url": media.get("url"),
                        "alt": media.get("alt_text", ""),
                        "sha256": content_hash,
                    }
                )
                continue
            info = probe(path)
            duration = float(info.get("format", {}).get("duration", 0))
            has_audio = any(
                s.get("codec_type") == "audio" for s in info.get("streams", [])
            )
            if duration <= 0:
                raise RuntimeError("Video duration unavailable.")
            recognition = None
            wav = None
            if has_audio:
                progress("preparing_audio")
                wav = audio_file(path, content_hash, duration)
                try:
                    progress("transcribing")
                    recognition, result = transcribe(wav, directory)
                    warnings.extend(result.get("warnings", []))
                    paragraphs.append(
                        "## Transcript\n\n"
                        + (recognition["text"] or "No speech recognized.")
                    )
                    words = recognition.get("words", [])
                    for i in range(0, len(words), 65):
                        group = words[i : i + 75]
                        if not group:
                            continue
                        body = " ".join(w["word"] for w in group)
                        add(
                            "transcript",
                            body,
                            {
                                "path": str(path),
                                "start_seconds": group[0]["startTime"],
                                "end_seconds": group[-1]["endTime"],
                            },
                            text="title: none | text: " + body,
                        )
                except (Paused, DiskSpaceError):
                    raise
                except Exception as e:
                    warnings.append("Transcription: " + str(e))
            else:
                paragraphs.append(
                    "## Video\n\nNo audio stream; transcription is not applicable."
                )
            cap = cv2.VideoCapture(str(path))
            for start in np.arange(0, duration, 8):
                end = min(float(start) + 8, duration)
                moments = np.arange(start + 0.5, end, 1)
                frames = []
                if not len(moments):
                    moments = np.array([(start + end) / 2])
                for moment in moments:
                    cap.set(cv2.CAP_PROP_POS_MSEC, float(moment * 1000))
                    ok, bgr = cap.read()
                    if not ok:
                        raise RuntimeError(f"Frame decode failed at {moment:.2f}s.")
                    frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                preview = directory / "assets" / f"{media['media_key']}-{start:.1f}.jpg"
                output = io.BytesIO()
                Image.fromarray(frames[0]).save(output, format="JPEG")
                atomic(preview, output.getvalue())
                meta = {
                    "path": str(path),
                    "preview": str(preview),
                    "start_seconds": float(start),
                    "end_seconds": end,
                    "frame_times": moments.tolist(),
                    "sha256": content_hash,
                }
                sample = np.stack(frames)
                add(
                    "video",
                    "",
                    meta,
                    text="<|video|>",
                    videos=sample,
                    do_sample_frames=False,
                )
                spoken = " ".join(
                    w["word"]
                    for w in (recognition or {}).get("words", [])
                    if start <= w["startTime"] < end
                )
                mixed = {
                    "text": text[:1600] + " " + spoken + " <|video|>",
                    "videos": sample,
                    "do_sample_frames": False,
                }
                if wav is not None:
                    with sf.SoundFile(wav) as audio_source:
                        audio_source.seek(int(start * 16000))
                        audio = audio_source.read(
                            int((end - start) * 16000), dtype="float32"
                        )
                    if len(audio):
                        add(
                            "audio",
                            "",
                            {**meta, "audio_decoder": "timeline-v1"},
                            text="<|audio|>",
                            audio=audio,
                            sampling_rate=16000,
                        )
                        mixed.update(
                            text=mixed["text"] + " <|audio|>",
                            audio=audio,
                            sampling_rate=16000,
                        )
                add(
                    "video-context",
                    spoken,
                    {**meta, "audio_decoder": "timeline-v1"} if wav else meta,
                    **mixed,
                )
                try:
                    progress("reading_images", "Video text")
                    screen = ocr(preview)
                    if screen.strip():
                        add_text(screen, "ocr", meta)
                except (Paused, DiskSpaceError):
                    raise
                except Exception as e:
                    warnings.append("Video OCR: " + str(e))
            cap.release()
            paragraphs.append(f"[Saved video]({path})")
        except (Paused, DiskSpaceError):
            raise
        except Exception as e:
            warnings.append(f"Media {media['media_key']}: {e}")
    urls = {}
    captured_ids = {post["id"], *(p["id"] for p in context)}
    from urllib.parse import urlsplit
    import re

    for origin in [post, *context]:
        for url in links(origin):
            parsed = urlsplit(url)
            if parsed.hostname in (
                "x.com",
                "www.x.com",
                "twitter.com",
                "www.twitter.com",
                "t.co",
            ):
                match = re.search(r"/(?:status|statuses)/(\d+)", parsed.path)
                if match and match[1] not in captured_ids:
                    warnings.append(f"Linked X post not expanded: {url}")
                continue
            urls.setdefault(url, []).append(f"https://x.com/i/status/{origin['id']}")
    for url in dict.fromkeys(urls):
        try:
            progress("downloading", "Linked pages and images")
            linked = external_document(url, directory)
            warnings.extend(linked["warnings"])
            images.extend(linked["images"])
            add_text(
                linked["text"],
                "linked-document",
                {"url": url, "path": linked["path"], "linked_from": urls[url]},
            )
            paragraphs.append(
                f"## Linked source\n\nSource: {url}\nLinked from: {', '.join(urls[url])}\n\n"
                + linked["text"]
            )
        except (Paused, DiskSpaceError):
            raise
        except Exception as e:
            warnings.append(f"Linked source {url}: {e}")
            paragraphs.append(f"## Linked source\n\nSource: {url}\n\nUnavailable: {e}")
    for im in images:
        try:
            with Image.open(im["path"]) as source:
                img = source.convert("RGB")
            meta = {
                **im,
                "sha256": __import__("hashlib")
                .sha256(Path(im["path"]).read_bytes())
                .hexdigest(),
            }
            add("image", im.get("alt", ""), meta, text="<|image|>", images=img)
            progress("reading_images")
            screen = ocr(im["path"])
            if screen.strip():
                add_text(screen, "ocr", meta)
            paragraphs.append(f"![{im.get('alt', 'Saved image')}]({im['path']})")
        except (Paused, DiskSpaceError):
            raise
        except Exception as e:
            warnings.append("Image processing: " + str(e))
    if article and images:
        # Link every article image to overlapping text passages within the context budget.
        for _, _, part in segments(text, 2800, 200):
            for offset in range(0, len(images), 3):
                group = images[offset : offset + 3]
                try:
                    add(
                        "article-context",
                        part,
                        {
                            "images": [
                                {
                                    "path": i["path"],
                                    "sha256": __import__("hashlib")
                                    .sha256(Path(i["path"]).read_bytes())
                                    .hexdigest(),
                                }
                                for i in group
                            ]
                        },
                        text=part + " " + " ".join("<|image|>" for _ in group),
                        images=[Image.open(i["path"]).convert("RGB") for i in group],
                    )
                except (Paused, DiskSpaceError):
                    raise
                except Exception as e:
                    warnings.append("Joint article embedding: " + str(e))
    warnings = list(dict.fromkeys(warnings))
    progress("saving")
    paragraphs.append(
        "## Coverage\n\n"
        + (
            "\n".join("- " + w for w in warnings)
            or "Processed the returned source and accessible attachments."
        )
    )
    atomic(directory / "content.md", "\n\n".join(paragraphs))
    atomic(directory / "coverage.json", warnings)
    with db() as c:
        previous_enrichment = c.execute(
            "SELECT vector,model FROM chunks WHERE id=?", (item + ":enrichment",)
        ).fetchone()
        c.execute("DELETE FROM chunks WHERE item=?", (item,))
        c.execute("DELETE FROM fts WHERE item=?", (item,))
        for entry in entries:
            c.execute(
                "INSERT INTO chunks VALUES (?,?,?,?,?,?,?)",
                (
                    entry["id"],
                    item,
                    entry["kind"],
                    entry["text"],
                    json.dumps(entry["metadata"]),
                    entry["vector"],
                    FINGERPRINT,
                ),
            )
            if entry["text"]:
                c.execute(
                    "INSERT INTO fts VALUES (?,?,?)", (entry["id"], item, entry["text"])
                )
        c.execute(
            "UPDATE items SET state=?,processed_hash=?,refreshed=?,coverage=?,processor=? WHERE id=?",
            (
                "partial" if warnings else "ready",
                row["hash"],
                time.time(),
                json.dumps(warnings),
                PROCESSOR_VERSION,
                item,
            ),
        )
        from enrichment import restore_index

        restore_index(c, item, previous_enrichment)
    active_keys = {e["id"].split(":", 1)[1] + ".npy" for e in entries}
    for path in model_cache.glob("*.npy"):
        if path.name not in active_keys:
            path.unlink()
    set_setting("processing", None)
    return {"entries": len(entries), "warnings": warnings}
