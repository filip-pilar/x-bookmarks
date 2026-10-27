"""Durable per-source work, progressive evidence, and bounded disposable media."""
import io, json, math, os, shutil, threading, time, uuid
from pathlib import Path
from store import HOME, db, atomic, fingerprint, content_fingerprint, setting, set_setting, config
from sources import policy, checksum, DiskSpaceError, context_request

WAKE = threading.Event()
CACHE = HOME / "cache/media-v1"
CACHE_LIMIT = 2 * 1024**3
ASSET_LIMIT = 192 * 1024**2


def migrate(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,state TEXT,created REAL);
    CREATE TABLE IF NOT EXISTS stages(id TEXT PRIMARY KEY,item TEXT,source TEXT,kind TEXT,
      revision TEXT,run TEXT,state TEXT,payload TEXT,result TEXT DEFAULT '{}',attempts INTEGER DEFAULT 0,
      next_at REAL DEFAULT 0,error TEXT,updated REAL);
    CREATE INDEX IF NOT EXISTS stages_work ON stages(state,next_at,kind);
    CREATE INDEX IF NOT EXISTS stages_item ON stages(item);
    CREATE INDEX IF NOT EXISTS stages_run ON stages(run);
    CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,descriptor TEXT,sha256 TEXT,state TEXT,bytes INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS observations(source TEXT,at REAL,metrics TEXT,PRIMARY KEY(source,at));
    CREATE TABLE IF NOT EXISTS captured_sources(id TEXT,revision TEXT,payload TEXT,at REAL,PRIMARY KEY(id,revision));
    """)
    if "run" not in {r[1] for r in c.execute("PRAGMA table_info(jobs)")}:
        c.execute("ALTER TABLE jobs ADD COLUMN run TEXT")
        c.execute("UPDATE jobs SET run=id")
        c.execute("INSERT OR IGNORE INTO runs SELECT DISTINCT run,'paused',created FROM jobs")
        for row in c.execute("SELECT id,raw,hash,processed_hash FROM items").fetchall():
            raw = json.loads(row["raw"])
            revision = content_fingerprint(raw["post"], raw.get("includes", {}).get("media", []))
            c.execute("UPDATE items SET hash=?,processed_hash=CASE WHEN processed_hash=? THEN ? ELSE processed_hash END WHERE id=?",
                      (revision, row["hash"], revision, row["id"]))
        c.execute("INSERT OR REPLACE INTO settings VALUES ('paused','true')")
    c.execute("UPDATE stages SET state='queued' WHERE state='running'")
    c.execute("CREATE INDEX IF NOT EXISTS jobs_run ON jobs(run)")


def authorize(run):
    with db() as c:
        if setting("paused", True):
            c.execute("UPDATE runs SET state='paused' WHERE state='running'")
        c.execute("INSERT INTO runs VALUES (?,'running',?) ON CONFLICT(id) DO UPDATE SET state='running'", (run, time.time()))
        c.execute("INSERT OR REPLACE INTO settings VALUES ('paused','false')")
    WAKE.set()


def snapshot_metrics(c, post, media):
    for source, metrics in [(post["id"], post.get("public_metrics")), *[(m["media_key"], m.get("public_metrics")) for m in media]]:
        if metrics is not None:
            numeric = {k: v for k, v in metrics.items() if isinstance(v, int) and v >= 0}
            c.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?)", (source, time.time(), json.dumps(numeric)))


def post_text(post):
    article = post.get("article", {})
    parts = [post.get("note_tweet", {}).get("text") or post.get("text", ""), article.get("title", ""), article.get("plain_text", "")]
    return "\n\n".join(dict.fromkeys(part for part in parts if part))


def chunks_of(text, size=1800):
    for start in range(0, len(text), size - 160):
        yield start, text[start:start + size]


def put_chunk(c, item, source, revision, kind, text, meta=None, vector=None, suffix=""):
    from embedding import FINGERPRINT
    meta = {"source_id": source, "revision": revision, **(meta or {})}
    key = fingerprint([item, source, revision, kind, suffix])
    previous = c.execute("SELECT vector,model FROM chunks WHERE id=?", (key,)).fetchone()
    if vector is None and previous:
        vector = previous["vector"]
    c.execute("INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?)", (key, item, kind, text,
        json.dumps(meta), vector, FINGERPRINT if vector is not None else "pending"))
    c.execute("DELETE FROM fts WHERE id=?", (key,))
    if text:
        c.execute("INSERT INTO fts VALUES (?,?,?)", (key, item, text))
    __import__("scope").own(c, key, meta.get("owner_post") or meta.get("linked_from") or (source if kind in ("text", "context") else None))
    return key


def publish_text(c, item, source, revision, kind, text, meta=None):
    for start, part in chunks_of(text):
        put_chunk(c, item, source, revision, kind, part, {**(meta or {}), "start_char": start, "end_char": start + len(part)}, suffix=str(start))


def publish_post(c, item, post, row_title=None):
    source = post["id"]
    revision = content_fingerprint(post, [])
    c.execute("INSERT OR REPLACE INTO captured_sources VALUES (?,?,?,?)", (source, revision, json.dumps(post), time.time()))
    # A new source revision replaces that source's current text, not unrelated media evidence.
    old = [r[0] for r in c.execute("SELECT id FROM chunks WHERE item=? AND kind IN ('text','context') AND json_extract(metadata,'$.source_id')=? AND json_extract(metadata,'$.revision')!=?", (item, source, revision))]
    for key in old:
        c.execute("DELETE FROM chunks WHERE id=?", (key,))
        c.execute("DELETE FROM fts WHERE id=?", (key,))
    publish_text(c, item, source, revision, "text" if source == item else "context", post_text(post), {"url": "https://x.com/i/status/" + source})


def queue(c, item, source, kind, revision, payload, run):
    key = fingerprint([item, source, kind, revision])
    c.execute("INSERT INTO stages(id,item,source,kind,revision,run,state,payload,updated) VALUES (?,?,?,?,?,?,'queued',?,?) ON CONFLICT(id) DO UPDATE SET run=excluded.run",
              (key, item, source, kind, revision, run, json.dumps(payload), time.time()))
    return key


def media_keys(post):
    article = post.get("article", {})
    return set(post.get("attachments", {}).get("media_keys", [])) | set(article.get("media_entities", [])) | ({article["cover_media"]} if article.get("cover_media") else set())


def capture_post(c, item, post, media, run, mode, users=()):
    from context import links
    from urllib.parse import urlsplit
    media = [m for m in media if m.get("media_key") in media_keys(post)]
    c.execute("INSERT OR REPLACE INTO post_presentations VALUES (?,?)", (post["id"], json.dumps({"post": post, "includes": {"media": media, "users": list(users)}})))
    publish_post(c, item, post)
    snapshot_metrics(c, post, media)
    revision = content_fingerprint(post, media)
    queue(c, item, post["id"], "text", revision, {}, run)
    available = {m["media_key"]: m for m in media}
    for key in media_keys(post):
        descriptor = available.get(key)
        if not descriptor:
            queue(c, item, post["id"], "media_lookup", revision, {"mode": mode}, run)
            continue
        asset = fingerprint([post["id"], key])
        c.execute("INSERT OR IGNORE INTO assets VALUES (?,?,'','pending',0)", (asset, json.dumps(descriptor)))
        if descriptor.get("alt_text"):
            publish_text(c, item, asset, fingerprint(descriptor["alt_text"]), "alt-text", descriptor["alt_text"], {"url": "https://x.com/i/status/" + post["id"], "owner_post": post["id"]})
            queue(c, item, asset, "text", fingerprint(descriptor["alt_text"]), {}, run)
        queue(c, item, asset, "download", fingerprint([content_fingerprint({}, [descriptor]), run if mode == "refresh" else ""]), {"media": descriptor, "post": post["id"], "mode": mode}, run)
    for url in links(post):
        if urlsplit(url).hostname in ("x.com", "twitter.com", "www.x.com", "www.twitter.com", "t.co"):
            continue
        source = "page:" + fingerprint(url)
        queue(c, item, source, "page", fingerprint([url, mode == "refresh" and run]), {"url": url, "post": post["id"], "mode": mode}, run)


def prepare(item, retry=False, mode="repair", generation=""):
    from xapi import get, FIELDS
    run = generation or uuid.uuid4().hex
    with db() as c:
        row = dict(c.execute("SELECT * FROM items WHERE id=?", (item,)).fetchone())
    raw = json.loads(row["raw"])
    prepared_hash = row["hash"]
    if mode == "refresh":
        with policy(mode, run):
            result = context_request("/tweets/" + item, FIELDS, get)
        if not result.get("data") or result.get("errors"):
            raise RuntimeError("Incomplete refresh; prior evidence retained.")
        from store import upsert_page
        with db() as c:
            upsert_page(c, {**result, "data": [result["data"]]}, run)
        raw = {"post": result["data"], "includes": result.get("includes", {})}
        prepared_hash = content_fingerprint(raw["post"], raw["includes"].get("media", []))
    with db() as c:
        capture_post(c, item, raw["post"], raw.get("includes", {}).get("media", []), run, mode)
        for ref in raw["post"].get("referenced_tweets", []):
            if ref.get("type") in ("replied_to", "quoted"):
                queue(c, item, ref["id"], "context", fingerprint([ref["id"], mode == "refresh" and run]), {"mode": mode}, run)
        c.execute("UPDATE items SET processed_hash=?,processor='5',state='processing' WHERE id=?", (prepared_hash, item))
    WAKE.set()
    return {"queued_stages": True}


def asset_path(asset):
    if len(asset) != 64 or any(x not in "0123456789abcdef" for x in asset):
        raise ValueError("Invalid asset identifier.")
    return CACHE / asset


def cache_size():
    size = 0
    for path in CACHE.rglob("*"):
        try:
            if path.is_file():
                size += path.stat().st_size
        except FileNotFoundError:
            pass
    return size


def finish_runs():
    with db() as c:
        c.execute("UPDATE runs SET state='complete' WHERE state='running' AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.run=runs.id AND j.state NOT IN ('complete','cancelled')) AND NOT EXISTS(SELECT 1 FROM stages s WHERE s.run=runs.id AND s.state!='complete')")


def reserve_cache(size):
    limit = int(config().get("media_cache_bytes", CACHE_LIMIT))
    if size + 8 * 1024**2 > limit:
        raise CapacityError("Required scratch exceeds the configured media budget.")
    if cache_size() + size + 8 * 1024**2 > limit:
        raise DiskSpaceError("Temporary media budget reached; finish pending descriptions or clear selected failed media.")
    from sources import reserve_space
    reserve_space(size)


def save_stage(stage, result):
    with db() as c:
        c.execute("UPDATE stages SET state='complete',result=?,error=NULL,updated=? WHERE id=?", (json.dumps(result), time.time(), stage["id"]))
    refresh_item(stage["item"])
    finish_runs()


def refresh_item(item):
    with db() as c:
        counts = {r[0]: r[1] for r in c.execute("SELECT state,count(*) FROM stages WHERE item=? GROUP BY state", (item,))}
        pending = any(counts.get(s) for s in ("queued", "running", "waiting", "blocked", "browser"))
        state = "processing" if pending else "partial" if counts.get("failed") or counts.get("unavailable") else "ready"
        c.execute("UPDATE items SET state=?,refreshed=? WHERE id=?", (state, time.time(), item))
        from enrichment import source_revision
        c.execute("UPDATE items SET source_revision=? WHERE id=?", (source_revision(c, item), item))


def clean_asset(asset):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute("SELECT state FROM stages WHERE source=? OR json_extract(payload,'$.asset')=?", (asset, asset)).fetchall()
        if not rows or any(r[0] != "complete" for r in rows):
            return False
        directory = asset_path(asset)
        # This directory contains only v1-managed scratch; never old items/assets or sources/.
        shutil.rmtree(directory, ignore_errors=True)
        c.execute("UPDATE assets SET state='evicted',bytes=0 WHERE id=?", (asset,))
    return True


def reuse_completed_asset(stage):
    """Attach compatible committed media evidence without downloading or describing it twice."""
    asset = stage["source"]
    with db() as c:
        previous = c.execute("SELECT item FROM stages WHERE source=? AND kind='download' AND revision=? AND state='complete' AND item!=? LIMIT 1", (asset, stage["revision"], stage["item"])).fetchone()
        if not previous:
            return None
        rows = c.execute("SELECT * FROM stages WHERE item=? AND (source=? OR json_extract(payload,'$.asset')=?)", (previous[0], asset, asset)).fetchall()
        if not rows or any(r["state"] != "complete" for r in rows):
            return None
        evidence = c.execute("SELECT * FROM chunks WHERE item=? AND json_extract(metadata,'$.asset_id')=?", (previous[0], asset)).fetchall()
        for row in evidence:
            meta = json.loads(row["metadata"])
            put_chunk(c, stage["item"], meta["source_id"], meta["revision"], row["kind"], row["text"], meta, row["vector"], suffix=row["id"])
        for row in rows:
            if row["kind"] == "download" or (row["kind"] == "text" and not json.loads(row["payload"]).get("asset")):
                continue
            key = queue(c, stage["item"], row["source"], row["kind"], row["revision"], json.loads(row["payload"]), stage["run"])
            c.execute("UPDATE stages SET state='complete',result=?,error=NULL WHERE id=?", (row["result"], key))
        return {"asset": asset, "reused_completed_evidence": True, "chunks": len(evidence)}


def execute(stage):
    import numpy as np
    from PIL import Image
    from embedding import encode, FINGERPRINT
    from xapi import get, FIELDS
    from content import probe, ocr, audio_file, transcribe
    item, source, kind, revision, run = (stage[k] for k in ("item", "source", "kind", "revision", "run"))
    p = json.loads(stage["payload"])
    if kind in ("context", "media_lookup"):
        with policy(p.get("mode", "repair"), run):
            result = context_request("/tweets/" + source, FIELDS, get)
        if not result.get("data"):
            raise SourceUnavailable("source_unavailable")
        if result.get("errors"):
            raise RuntimeError("partial_source_response")
        if kind == "media_lookup" and media_keys(result["data"]) - {m["media_key"] for m in result.get("includes", {}).get("media", [])}:
            raise RuntimeError("media_expansion_missing")
        with db() as c:
            capture_post(c, item, result["data"], result.get("includes", {}).get("media", []), run, p.get("mode", "repair"), result.get("includes", {}).get("users", []))
        return {"captured": source}
    if kind == "text":
        with db() as c:
            rows = c.execute("SELECT * FROM chunks WHERE item=? AND json_extract(metadata,'$.source_id')=? AND text!='' AND (vector IS NULL OR model!=?)", (item, source, FINGERPRINT)).fetchall()
        for row in rows:
            if setting("paused", True):
                raise InterruptedError("processing_paused")
            vector = encode(text="title: none | text: " + row["text"])[0].tobytes()
            with db() as c:
                c.execute("UPDATE chunks SET vector=?,model=? WHERE id=?", (vector, FINGERPRINT, row["id"]))
        return {"embedded": len(rows)}
    if kind == "page":
        from pages import capture
        result = capture(p["url"], p.get("mode", "repair"), run)
        with db() as c:
            publish_text(c, item, source, fingerprint(result["text"]), "linked-document", result["text"], {"url": p["url"], "linked_from": p["post"], "method": result["method"], "coverage": result.get("coverage", [])})
            queue(c, item, source, "text", fingerprint(result["text"]), {}, run)
            for im in result.get("images", []):
                asset = fingerprint([source, im["url"]])
                media = {**im, "type": "photo", "media_key": asset}
                c.execute("INSERT OR IGNORE INTO assets VALUES (?,?,'','pending',0)", (asset, json.dumps(media)))
                queue(c, item, asset, "download", fingerprint(im), {"media": media, "post": p["post"], "mode": p.get("mode", "repair"), "source_url": p["url"]}, run)
        return result
    directory = asset_path(p.get("asset", source))
    asset = p.get("asset", source)
    if kind == "download":
        if p.get("mode") != "refresh":
            reused = reuse_completed_asset(stage)
            if reused is not None:
                return reused
        from pages import download
        media = p["media"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("original.jpg" if media["type"] == "photo" else "original.mp4")
        if p.get("mode") != "refresh":
            with db() as c:
                previous = c.execute("SELECT sha256 FROM assets WHERE id=?", (asset,)).fetchone()
            candidate = directory / ("source-" + previous[0] + path.suffix) if previous and previous[0] else None
            if candidate and candidate.is_file():
                path = candidate
        if not path.exists() and p.get("mode") != "refresh":
            key = media.get("media_key", "")
            if __import__("re").fullmatch(r"[0-9]+_[0-9]+", key):
                old = HOME / "items" / item / "assets" / (key + (".image" if media["type"] == "photo" else ".mp4"))
                if old.is_file():
                    reserve_cache(old.stat().st_size)
                    shutil.copyfile(old, path)
        if not path.exists():
            if p.get("mode") == "rebuild" or config().get("offline"):
                raise RuntimeError("source_retrieval_required")
            reserve_cache(ASSET_LIMIT)
            if media["type"] == "photo":
                url = media["url"]
            else:
                variants = sorted((v for v in media.get("variants", []) if v.get("content_type") == "video/mp4"), key=lambda v: v.get("bit_rate", 0))
                if not variants:
                    raise SourceUnavailable("media_variant_unavailable")
                url = next((v["url"] for v in variants if v.get("bit_rate", 0) >= 800000), variants[-1]["url"])
            download(url, path, ASSET_LIMIT)
        if media["type"] == "photo":
            try:
                from raster import convert_svg
                convert_svg(path)
                with Image.open(path) as im:
                    tiny = max(im.size) <= 2
                    im.verify()
            except (OSError, ValueError):
                path.unlink(missing_ok=True)
                raise RuntimeError("invalid_image_bytes") from None
            if tiny:
                path.unlink(missing_ok=True)
                with db() as c:
                    c.execute("UPDATE assets SET state='evicted',bytes=0 WHERE id=?", (asset,))
                return {"asset": asset, "coverage": ["Tracking-sized image omitted (2 pixels or smaller)."]}
        else:
            try:
                info = probe(path)
            except (RuntimeError, ValueError):
                path.unlink(missing_ok=True)
                raise RuntimeError("invalid_video_bytes") from None
        sha = checksum(path)
        immutable = directory / ("source-" + sha + path.suffix)
        if path != immutable:
            if immutable.exists():
                path.unlink()
            else:
                path.replace(immutable)
            path = immutable
        with db() as c:
            c.execute("UPDATE assets SET sha256=?,state='cached',bytes=? WHERE id=?", (sha, path.stat().st_size, asset))
            common = {"asset": asset, "path": path.name, "post": p["post"], "sha256": sha, "source_url": p.get("source_url", "https://x.com/i/status/" + p["post"])}
            if media["type"] == "photo":
                for task in ("image", "ocr", "caption"):
                    queue(c, item, asset, task, sha, common, run)
            else:
                duration = float(info.get("format", {}).get("duration", 0))
                if duration <= 0 or duration > 4 * 3600:
                    raise SourceUnavailable("video_duration_unsupported")
                audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
                common.update(duration=duration, audio=audio)
                if audio:
                    queue(c, item, asset, "transcript", sha, common, run)
                for start in range(0, math.ceil(duration), 8):
                    segment = {**common, "start": start, "end": min(start + 8, duration)}
                    for task in ("video", "video_ocr", "video_caption"):
                        queue(c, item, asset + ":" + str(start), task, sha, segment, run)
                    if audio:
                        queue(c, item, asset + ":" + str(start), "audio", sha, segment, run)
        return {"asset": asset, "sha256": sha}
    path = directory / p["path"]
    if not path.is_file():
        raise RuntimeError("source_retrieval_required")
    meta = {"owner_post": p["post"], "asset_id": asset, "url": p.get("source_url", "https://x.com/i/status/" + p["post"]), "sha256": p["sha256"]}
    text, vector, evidence_kind = "", None, kind
    if kind in ("image", "ocr", "caption"):
        with Image.open(path) as image:
            if max(image.size) <= 2:
                return {"asset": asset, "coverage": ["Tracking-sized image omitted (2 pixels or smaller)."]}
        if kind == "image":
            with Image.open(path) as image:
                vector = encode(text="<|image|>", images=image.convert("RGB"))[0].tobytes()
        elif kind == "ocr":
            text = ocr(path)
        else:
            import subscription
            result = subscription.describe([(path, None)])
            text, evidence_kind = result["text"], "description"
            meta.update(generated=True, model=result["model"], captured_at=result["at"])
    elif kind == "transcript":
        reserve_cache(int(p["duration"] * 160000) + 16 * 1024**2)
        wav = audio_file(path, p["sha256"], p["duration"])
        recognition, result = transcribe(wav, directory)
        words = recognition.get("words", [])
        with db() as c:
            for offset in range(0, len(words), 65):
                group = words[offset:offset + 75]
                body = " ".join(w["word"] for w in group)
                put_chunk(c, item, source, revision, "transcript", body, {**meta, "start_seconds": group[0]["startTime"], "end_seconds": group[-1]["endTime"]}, suffix=str(offset))
            if not words and recognition.get("text", "").strip():
                publish_text(c, item, source, revision, "transcript", recognition["text"], meta)
            queue(c, item, source, "text", fingerprint([revision, "transcript"]), {"asset": asset}, run)
        return {"text": recognition.get("text", ""), "words": words, "warnings": result.get("warnings", [])}
    else:
        import cv2
        start, end = p["start"], p["end"]
        times = [min(start + n + .5, (start + end) / 2 if end - start < 1 else end - .1) for n in range(max(1, math.ceil(end - start)))]
        meta.update(start_seconds=start, end_seconds=end, frame_times=times)
        if kind == "audio":
            import soundfile as sf
            from content import command
            wav = directory / f"audio-{start}.wav"
            reserve_cache(512 * 1024)
            command(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", path, "-t", str(end - start), "-ac", "1", "-ar", "16000", wav])
            audio, rate = sf.read(wav, dtype="float32")
            vector = encode(text="<|audio|>", audio=audio, sampling_rate=rate)[0].tobytes()
            wav.unlink()
        else:
            frames, paths = [], []
            cap = cv2.VideoCapture(str(path))
            try:
                reserve_cache(4 * 1024**2)
                for t in times:
                    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                    ok, frame = cap.read()
                    if not ok:
                        raise RuntimeError("frame_decode_failed")
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    image = Image.fromarray(rgb)
                    image.thumbnail((1280, 1280))
                    frames.append(np.array(image))
                    target = directory / f"{kind}-frame-{t:.2f}.jpg"
                    image.save(target, "JPEG", quality=85)
                    paths.append((target, t))
                if kind == "video":
                    vector = encode(text="<|video|>", videos=np.stack(frames), do_sample_frames=False)[0].tobytes()
                elif kind == "video_ocr":
                    text, evidence_kind = "\n".join(dict.fromkeys(ocr(f) for f, _ in paths)), "ocr"
                elif kind == "video_caption":
                    import subscription
                    with db() as c:
                        transcript = c.execute("SELECT result FROM stages WHERE item=? AND source=? AND kind='transcript' AND state='complete'", (item, asset)).fetchone()
                    words = json.loads(transcript[0]).get("words", []) if transcript else []
                    spoken = " ".join(w["word"] for w in words if start <= w["startTime"] < end)
                    result = subscription.describe(paths, "Video sampled once per second. Speech in this segment: " + spoken)
                    text, evidence_kind = result["text"], "description"
                    meta.update(generated=True, model=result["model"], captured_at=result["at"])
                else:
                    raise ValueError("Unknown processing stage.")
            finally:
                cap.release()
                for f, _ in paths:
                    f.unlink(missing_ok=True)
    with db() as c:
        put_chunk(c, item, source, revision, evidence_kind, text, meta, vector)
        if text:
            queue(c, item, source, "text", fingerprint([revision, kind, text]), {"asset": asset}, run)
    return {"text": text, "asset": asset, "evidence_kind": evidence_kind, "metadata": meta}


class CapacityError(RuntimeError):
    pass


class SourceUnavailable(RuntimeError):
    pass


def work_once(lane="local"):
    from pages import BrowserNeeded
    from subscription import PlanError
    from xapi import RetryLater, XError
    if setting("paused", True):
        return False
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        condition = "s.kind IN ('caption','video_caption')" if lane == "description" else "s.kind NOT IN ('caption','video_caption')"
        condition += " AND (json_extract(s.payload,'$.asset') IS NULL OR NOT EXISTS(SELECT 1 FROM stages d WHERE d.source=json_extract(s.payload,'$.asset') AND d.kind='download' AND d.run=s.run AND d.state!='complete'))"
        if lane == "description":
            condition += " AND NOT EXISTS(SELECT 1 FROM stages t WHERE t.kind='transcript' AND t.item=s.item AND t.source=json_extract(s.payload,'$.asset') AND t.state IN ('queued','running','waiting'))"
        row = c.execute("SELECT s.* FROM stages s JOIN runs r ON r.id=s.run WHERE r.state='running' AND s.state IN ('queued','waiting') AND s.next_at<=? AND " + condition + " ORDER BY CASE s.kind WHEN 'text' THEN 0 WHEN 'context' THEN 1 WHEN 'transcript' THEN 2 WHEN 'download' THEN 4 ELSE 3 END,s.updated LIMIT 1", (time.time(),)).fetchone()
        if not row:
            return False
        stage = dict(row)
        c.execute("UPDATE stages SET state='running',updated=? WHERE id=?", (time.time(), stage["id"]))
    try:
        result = execute(stage)
        save_stage(stage, result)
        asset = json.loads(stage["payload"]).get("asset") or (stage["source"] if stage["kind"] == "download" else None)
        if asset:
            try:
                clean_asset(asset)
            except OSError:
                pass  # Retain scratch on a cleanup error; evidence is already committed.
    except Exception as e:
        attempts = stage["attempts"] + 1
        state, delay = ("waiting" if attempts < 4 else "failed"), min(900, 15 * 2**attempts)
        code = type(e).__name__
        if isinstance(e, InterruptedError):
            state, code, delay, attempts = "queued", "processing_paused", 0, stage["attempts"]
        elif isinstance(e, CapacityError):
            state, code = "blocked", "temporary_budget_too_small"
        elif isinstance(e, BrowserNeeded):
            state, code = ("browser" if stage["kind"] == "page" else "unavailable"), e.reason
        elif isinstance(e, SourceUnavailable):
            state, code = "unavailable", str(e)
        elif isinstance(e, PlanError):
            code = e.code
            if any(x in code for x in ("sign_in", "consent", "selection", "eligible", "invalid_grant", "revoked", "unsupported")):
                state = "blocked"
            elif "rate_limit" in code or "usage_limit" in code or "429" in code or "quota" in code:
                state, delay = "waiting", 3600
        elif isinstance(e, DiskSpaceError):
            state, code, delay = "waiting", "temporary_storage_full", 300
        elif isinstance(e, RetryLater):
            delay = max(5, e.when - time.time())
            code = "x_retry_later"
        elif isinstance(e, XError):
            code = f"x_http_{e.status}"
            if e.status in (401, 402):
                state = "blocked"
            elif 400 <= e.status < 500 and e.status not in (408, 429):
                state = "unavailable"
        else:
            from diagnostics import safe_error
            code += ": " + safe_error(e)
        with db() as c:
            c.execute("UPDATE stages SET state=?,attempts=?,next_at=?,error=?,updated=? WHERE id=?", (state, attempts, time.time() + delay, code[:1000], time.time(), stage["id"]))
        refresh_item(stage["item"])
    return True


def loop(lane):
    while True:
        if not work_once(lane):
            if lane == "local" and not setting("paused", True):
                with db() as c:
                    candidates = [r[0] for r in c.execute("SELECT id FROM assets WHERE state='cached' LIMIT 100")]
                for asset in candidates:
                    try:
                        clean_asset(asset)
                    except OSError:
                        pass
            WAKE.wait(3)
            WAKE.clear()


def retry(item=None, run=None):
    if not item and not run:
        raise ValueError("Select an item or run to retry.")
    new_run = uuid.uuid4().hex
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        where, value = ("item=?", item) if item else ("run=?", run)
        stages = c.execute(f"UPDATE stages SET state='queued',attempts=0,next_at=0,error=NULL,run=? WHERE {where} AND state IN ('failed','waiting','blocked')", (new_run, value)).rowcount
        jobs = c.execute(f"UPDATE jobs SET state='queued',attempts=0,next_at=0,error=NULL,run=? WHERE {where} AND state='failed' AND NOT EXISTS(SELECT 1 FROM jobs active WHERE active.kind=jobs.kind AND active.item=jobs.item AND active.state IN ('queued','running','waiting'))", (new_run, value)).rowcount
    if stages or jobs:
        authorize(new_run)
    return {"retried": stages + jobs, "run": new_run if stages or jobs else None, "item": item}


def clearable_assets(c, item):
    assets = {r[0] for r in c.execute("SELECT DISTINCT coalesce(json_extract(payload,'$.asset'),CASE WHEN kind='download' THEN source END) FROM stages WHERE item=? AND state IN ('failed','blocked','unavailable')", (item,)) if r[0]}
    result = []
    for asset in assets:
        busy = c.execute("SELECT 1 FROM stages WHERE (source=? OR json_extract(payload,'$.asset')=?) AND state IN ('running','queued','waiting') LIMIT 1", (asset, asset)).fetchone()
        if busy:
            continue
        try:
            size = sum(p.stat().st_size for p in asset_path(asset).rglob('*') if p.is_file())
        except FileNotFoundError:
            continue
        if size:
            result.append((asset, size))
    return result


def clear_cache(item=None):
    if not item:
        raise ValueError("Select a bookmark before clearing temporary media.")
    removed = 0
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        for asset, size in clearable_assets(c, item):
            related = c.execute("SELECT * FROM stages WHERE source=? OR json_extract(payload,'$.asset')=?", (asset, asset)).fetchall()
            if any(r["state"] in ("running", "queued", "waiting") for r in related):
                continue
            path = asset_path(asset)
            if path.exists():
                removed += size
                shutil.rmtree(path)
            c.execute("UPDATE assets SET state='evicted',bytes=0 WHERE id=?", (asset,))
            if any(r["state"] != "complete" for r in related):
                c.execute("UPDATE stages SET state='blocked',error='source_retrieval_required' WHERE source=? AND kind='download'", (asset,))
    return {"removed_bytes": removed, "existing_originals_untouched": True}


def overview():
    import subscription
    with db() as c:
        counts = [dict(r) for r in c.execute("SELECT kind,state,count(*) AS count FROM stages GROUP BY kind,state")]
        issues = [dict(r) for r in c.execute("SELECT s.id,s.item,s.source,s.kind,s.state,s.attempts,s.next_at,s.error,s.run,i.title,i.author FROM stages s JOIN items i ON i.id=s.item WHERE i.active=1 AND s.state IN ('failed','blocked','browser','unavailable','waiting') ORDER BY CASE s.state WHEN 'failed' THEN 0 WHEN 'blocked' THEN 0 WHEN 'browser' THEN 0 ELSE 1 END,s.updated DESC LIMIT 100")]
        runs = [dict(r) for r in c.execute("SELECT * FROM runs WHERE EXISTS(SELECT 1 FROM jobs WHERE jobs.run=runs.id) OR EXISTS(SELECT 1 FROM stages WHERE stages.run=runs.id) ORDER BY CASE WHEN state='complete' THEN 1 ELSE 0 END,created DESC LIMIT 20")]
        import processing
        runs = [processing.run_details(c, run) for run in runs]
        processing_counts = processing.counts(c)
        progress = dict(c.execute("""SELECT
          count(DISTINCT CASE WHEN state IN ('queued','running','waiting','failed','blocked','browser') THEN item END) AS unfinished,
          count(DISTINCT CASE WHEN state IN ('failed','blocked','browser') THEN item END) AS needs_attention,
          count(DISTINCT CASE WHEN kind IN ('download','image','video','audio','transcript','ocr','video_ocr','caption','video_caption') AND state NOT IN ('complete','unavailable','cancelled') THEN item END) AS unfinished_media,
          count(DISTINCT CASE WHEN state='unavailable' THEN item END) AS unavailable
          FROM (SELECT s.item,s.kind,s.state FROM stages s JOIN items i ON i.id=s.item WHERE i.active=1
          UNION ALL SELECT j.item,j.kind,j.state FROM jobs j JOIN items i ON i.id=j.item WHERE i.active=1)""").fetchone())
        progress['syncs'] = c.execute("SELECT count(*) FROM jobs WHERE kind='sync' AND state NOT IN ('complete','cancelled')").fetchone()[0]
        progress['sync_attention'] = c.execute("SELECT count(*) FROM jobs WHERE kind='sync' AND state='failed'").fetchone()[0]
    return {**processing_counts, "stage_counts": counts, "stage_issues": issues, "runs": runs, "progress": progress, "chatgpt": subscription.status(), "temporary_bytes": cache_size(), "temporary_limit": int(config().get("media_cache_bytes", CACHE_LIMIT))}
