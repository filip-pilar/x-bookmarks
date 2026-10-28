import contextlib, hashlib, json, os, sqlite3, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = json.loads((ROOT / "package.json").read_text())["version"]
PROCESSOR_VERSION = "6"
HOME = Path(
    os.environ.get(
        "BOOKMARK_LIBRARY_HOME", "~/Library/Application Support/BookmarkLibrary"
    )
).expanduser()
HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
os.umask(0o077)


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb" if isinstance(value, bytes) else "w") as f:
            f.write(
                value
                if isinstance(value, (str, bytes))
                else json.dumps(value, ensure_ascii=False, indent=2)
            )
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


@contextlib.contextmanager
def db():
    c = sqlite3.connect(HOME / "library.sqlite", timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    path = HOME / "library.sqlite"
    if path.exists():
        with sqlite3.connect(path) as source:
            columns = {r[1] for r in source.execute("PRAGMA table_info(jobs)")}
            if columns and "run" not in columns:
                target = HOME / "backups/pre-0.7.0.sqlite"
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    with sqlite3.connect(target) as backup:
                        source.backup(backup)
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, title TEXT, author TEXT, url TEXT, raw TEXT,
          hash TEXT, active INTEGER DEFAULT 1, seen_run TEXT, state TEXT DEFAULT 'pending',
          processed_hash TEXT, refreshed REAL DEFAULT 0, coverage TEXT DEFAULT '[]', updated REAL);
        CREATE TABLE IF NOT EXISTS folders(id TEXT PRIMARY KEY, name TEXT, seen_run TEXT);
        CREATE TABLE IF NOT EXISTS members(folder TEXT, item TEXT, seen_run TEXT, PRIMARY KEY(folder,item));
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT, item TEXT, state TEXT,
          payload TEXT DEFAULT '{}', attempts INTEGER DEFAULT 0, next_at REAL DEFAULT 0, error TEXT,
          created REAL, updated REAL);
        CREATE UNIQUE INDEX IF NOT EXISTS pending_item ON jobs(kind,item) WHERE state IN ('queued','running','waiting');
        CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, item TEXT REFERENCES items(id) ON DELETE CASCADE,
          kind TEXT, text TEXT, metadata TEXT, vector BLOB, model TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(id UNINDEXED, item UNINDEXED, text);
        CREATE TABLE IF NOT EXISTS calls(time REAL, endpoint TEXT, status INTEGER, records INTEGER);
        CREATE TABLE IF NOT EXISTS scan_folders(run TEXT,id TEXT,name TEXT,PRIMARY KEY(run,id));
        CREATE TABLE IF NOT EXISTS scan_members(run TEXT,folder TEXT,item TEXT,PRIMARY KEY(run,folder,item));
        CREATE TABLE IF NOT EXISTS enrichment(item TEXT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
          revision TEXT NOT NULL, payload TEXT NOT NULL, updated REAL NOT NULL);
        """)
        columns = {r[1] for r in c.execute("PRAGMA table_info(items)")}
        if "details" not in {r[1] for r in c.execute("PRAGMA table_info(calls)")}:
            c.execute("ALTER TABLE calls ADD COLUMN details TEXT DEFAULT '{}'")
        for column, declaration in (
            ("processor", "TEXT DEFAULT ''"),
            ("membership", "TEXT DEFAULT 'present'"),
            ("source_revision", "TEXT DEFAULT ''"),
            ("folder_checked_at", "REAL"),
        ):
            if column not in columns:
                c.execute(f"ALTER TABLE items ADD COLUMN {column} {declaration}")
        if "folder_checked_at" not in columns:
            row = c.execute(
                "SELECT value FROM settings WHERE key='folder_inventory'"
            ).fetchone()
            inventory = json.loads(row[0]) if row else {}
            if inventory.get("complete") and inventory.get("at"):
                c.execute(
                    "UPDATE items SET folder_checked_at=? WHERE updated<=?",
                    (inventory["at"], inventory["at"]),
                )
        c.execute("UPDATE jobs SET state='queued' WHERE state='running'")
        from pipeline import migrate
        migrate(c)
        __import__("scope").migrate(c)
        from enrichment import source_revision

        for row in c.execute(
            "SELECT id FROM items WHERE processed_hash=hash AND source_revision='' "
        ).fetchall():
            revision = source_revision(c, row[0])
            c.execute(
                "UPDATE items SET source_revision=? WHERE id=?", (revision, row[0])
            )


def setting(key, default=None):
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(key, value):
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value))
        )


def config():
    p = HOME / "config.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_config(data):
    atomic(HOME / "config.json", data)


def coverage_kind(message):
    if (
        "bounded sample" in message
        or "network disabled" in message
        or "cached sample" in message
    ):
        return "sample"
    if any(
        note in message
        for note in (
            "inline positions were not supplied",
            "Readability extraction may omit",
            "expansion stopped",
            "extraction limit",
            "exceeded 8 GB",
            "Unsupported linked",
            "Linked X post not expanded",
            "Interactive page: saved page metadata",
        )
    ):
        return "limitation"
    if any(
        note in message
        for note in (
            "HTTP 404",
            "HTTP 410",
            "source unavailable",
            "Full X Article body not supplied",
        )
    ):
        return "unavailable"
    return "retryable"


def upsert_page(c, page, run=None, process=True):
    includes = page.get("includes", {})
    users = {u["id"]: u for u in includes.get("users", [])}
    posts = page.get("data", [])
    if isinstance(posts, dict):
        posts = [posts]
    for post in posts:
        if not __import__("re").fullmatch(r"[0-9]{1,25}", str(post.get("id", ""))):
            raise ValueError("X returned an invalid post identifier.")
        user = users.get(post.get("author_id"), {})
        # Includes may vary across pages; hash only this post and its own media.
        media_keys = set(post.get("attachments", {}).get("media_keys", []))
        article = post.get("article", {})
        media_keys.update(article.get("media_entities", []))
        if article.get("cover_media"):
            media_keys.add(article["cover_media"])
        own_media = [
            m for m in includes.get("media", []) if m.get("media_key") in media_keys
        ]
        own_media.sort(key=lambda media: media.get("media_key", ""))
        raw = {
            "post": post,
            "includes": {"media": own_media, "users": [user] if user else []},
        }
        h = content_fingerprint(post, own_media)
        title = (
            article.get("title")
            or post.get("note_tweet", {}).get("text", post.get("text", ""))[:140]
        )
        c.execute(
            """INSERT INTO items(id,title,author,url,raw,hash,seen_run,updated)
          VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,
          author=excluded.author,url=excluded.url,raw=excluded.raw,hash=excluded.hash,
          active=1,membership='present',seen_run=COALESCE(excluded.seen_run,items.seen_run),updated=excluded.updated""",
            (
                post["id"],
                title,
                user.get("username", post.get("author_id", "")),
                f"https://x.com/i/status/{post['id']}",
                json.dumps(raw),
                h,
                run,
                time.time(),
            ),
        )
        from pipeline import snapshot_metrics, publish_post
        snapshot_metrics(c, post, own_media)
        publish_post(c, post["id"], post, row_title=title)
        row = c.execute(
            "SELECT processed_hash,processor,coverage FROM items WHERE id=?",
            (post["id"],),
        ).fetchone()
        sample = any(
            coverage_kind(w) == "sample" or "Saved source missing" in w
            for w in json.loads(row["coverage"])
        )
        changed = row["processed_hash"] != h
        if process and (changed or sample or row["processor"] != PROCESSOR_VERSION):
            enqueue(
                c,
                "process",
                post["id"],
                {"mode": "repair" if changed or sample else "rebuild"},
                run=run,
            )


def enqueue(c, kind, item="", payload=None, run=None):
    import uuid

    existing = c.execute(
        "SELECT id FROM jobs WHERE kind=? AND item=? AND state IN ('queued','running','waiting')",
        (kind, item),
    ).fetchone()
    if existing:
        # Starting a new run must not move work out of another paused run.
        return existing[0]
    failed = c.execute(
        "SELECT id FROM jobs WHERE kind=? AND item=? AND state='failed' ORDER BY created DESC LIMIT 1",
        (kind, item),
    ).fetchone()
    if failed:
        c.execute(
            "UPDATE jobs SET state='queued',payload=?,attempts=0,next_at=0,error=NULL,updated=? WHERE id=?",
            (json.dumps(payload or {}), time.time(), failed[0]),
        )
        if run:
            c.execute("UPDATE jobs SET run=? WHERE id=?", (run, failed[0]))
        return failed[0]
    job = uuid.uuid4().hex
    c.execute(
        "INSERT INTO jobs(id,kind,item,state,payload,created,updated) VALUES (?,?,?,?,?,?,?)",
        (
            job,
            kind,
            item,
            "queued",
            json.dumps(payload or {}),
            time.time(),
            time.time(),
        ),
    )
    c.execute("UPDATE jobs SET run=? WHERE id=?", (run or job, job))
    return job


def content_fingerprint(post, media):
    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items() if k not in (
                "public_metrics", "non_public_metrics", "organic_metrics", "promoted_metrics",
                "variants", "preview_image_url") and not (k == "url" and "media_key" in value)}
        return [stable(v) for v in value] if isinstance(value, list) else value
    return fingerprint([stable(post), stable(media)])


def status():
    cfg = config()
    with db() as c:
        from enrichment import overview

        enrichment_status = overview(c)
        counts = {
            r[0]: r[1]
            for r in c.execute(
                "SELECT state,count(*) FROM items WHERE active=1 GROUP BY state"
            )
        }
        jobs = [
            dict(r)
            for r in c.execute(
                "SELECT j.id,j.kind,j.item,j.run,j.state,j.attempts,j.error,j.next_at,j.payload,i.title,i.author FROM jobs j LEFT JOIN items i ON i.id=j.item WHERE j.state NOT IN ('complete','cancelled') ORDER BY CASE j.kind WHEN 'sync' THEN 0 ELSE 1 END,j.created LIMIT 30"
            )
        ]
        job_counts = [
            dict(r)
            for r in c.execute(
                "SELECT kind,state,count(*) AS count FROM jobs WHERE state NOT IN ('complete','cancelled') GROUP BY kind,state"
            )
        ]
        processing_remaining = c.execute(
            "SELECT count(DISTINCT jobs.item) FROM jobs JOIN items ON items.id=jobs.item WHERE items.active=1 AND jobs.kind='process' AND jobs.state IN ('queued','running','waiting','failed')"
        ).fetchone()[0]
        usage = dict(
            c.execute(
                "SELECT count(*) AS requests,coalesce(sum(records),0) AS records FROM calls"
            ).fetchone()
        )
        recent_calls = [{**dict(r), "details": json.loads(r["details"] or "{}")}
                        for r in c.execute("SELECT * FROM calls ORDER BY time DESC LIMIT 10")]
        folders = c.execute("SELECT count(*) FROM folders").fetchone()[0]
        from embedding import FINGERPRINT

        vectors = c.execute(
            "SELECT count(*) FROM scoped_chunks WHERE model=?", (FINGERPRINT,)
        ).fetchone()[0]
        searchable = c.execute(
            "SELECT count(DISTINCT chunks.item) FROM scoped_chunks AS chunks JOIN items ON items.id=chunks.item WHERE items.active=1 AND (chunks.text!='' OR chunks.model=?)",
            (FINGERPRINT,),
        ).fetchone()[0]
        content = {
            r["kind"]: {
                "sources": r["sources"],
                "bookmarks": r["bookmarks"],
                "segments": r["segments"],
            }
            for r in c.execute(
                "SELECT kind,count(*) AS segments,count(DISTINCT chunks.item) AS bookmarks,count(DISTINCT coalesce(json_extract(metadata,'$.asset_id'),json_extract(metadata,'$.source_id'),json_extract(metadata,'$.path'),chunks.item)) AS sources FROM scoped_chunks AS chunks JOIN items ON items.id=chunks.item WHERE items.active=1 AND chunks.model=? AND chunks.vector IS NOT NULL GROUP BY kind",
                (FINGERPRINT,),
            )
        }
        last_processed = c.execute(
            "SELECT max(refreshed) FROM items WHERE active=1 AND processed_hash IS NOT NULL"
        ).fetchone()[0]
        issue_counts = {}
        issue_items = {
            kind: set() for kind in ("sample", "limitation", "unavailable", "retryable")
        }
        for row in c.execute(
            "SELECT id,coverage FROM items WHERE active=1 AND coverage!='[]'"
        ):
            for warning in set(json.loads(row["coverage"] or "[]")):
                issue_counts[warning] = issue_counts.get(warning, 0) + 1
                issue_items[coverage_kind(warning)].add(row["id"])
        issues = [
            {"message": message, "bookmarks": count, "kind": coverage_kind(message)}
            for message, count in sorted(
                issue_counts.items(), key=lambda x: (-x[1], x[0])
            )[:20]
        ]
        active_processing = c.execute(
            "SELECT items.id,items.title FROM jobs JOIN items ON items.id=jobs.item WHERE jobs.kind='process' AND jobs.state='running' AND items.active=1 LIMIT 1"
        ).fetchone()
        processing = None
        if active_processing:
            progress = setting("processing", {}) or {}
            processing = {
                "item": active_processing["id"],
                "title": active_processing["title"],
                **(progress if progress.get("item") == active_processing["id"] else {}),
            }
    global _storage_snapshot, _runtime_snapshot
    if time.monotonic() - _storage_snapshot[0] > 60:
        size = 0
        for root in (HOME / "items", HOME / "sources"):
            for folder, _, files in os.walk(root):
                for name in files:
                    try:
                        size += (Path(folder) / name).stat().st_size
                    except FileNotFoundError:
                        pass
        _storage_snapshot = (time.monotonic(), size)
    storage = _storage_snapshot[1]
    index_bytes = sum(p.stat().st_size for p in HOME.glob("library.sqlite*") if p.is_file())
    if time.monotonic() - _runtime_snapshot[0] > 300:
        runtime_bytes = 0
        for directory in (HOME / "models", HOME / "venv", HOME / "bin", HOME / "cache/transcribe/models"):
            for folder, _, files in os.walk(directory):
                for name in files:
                    try:
                        runtime_bytes += (Path(folder) / name).stat().st_size
                    except FileNotFoundError:
                        pass
        _runtime_snapshot = (time.monotonic(), runtime_bytes)
    runtime_bytes = _runtime_snapshot[1]
    return {
        **__import__("pipeline").overview(),
        "backend_version": VERSION,
        "enrichment": enrichment_status,
        "folder_inventory": setting("folder_inventory"),
        "connected": bool(cfg.get("account")) and not bool(setting("x_auth_error")),
        "account": cfg.get("account"),
        "counts": counts,
        "total": sum(counts.values()),
        "vectors": vectors,
        "searchable": searchable,
        "content": content,
        "last_processed": last_processed,
        "issues": issues,
        "issue_groups": len(issue_counts),
        "issue_counts": {kind: len(items) for kind, items in issue_items.items()},
        "folders": folders,
        "jobs": jobs,
        "job_counts": job_counts,
        "processing_remaining": processing_remaining,
        "paused": setting("paused", False),
        "pause_reason": setting("pause_reason", ""),
        "last_inventory": setting("last_inventory"),
        "full_inventory_at": setting("full_inventory_at"),
        "x_auth_error": setting("x_auth_error"),
        "sample_only": not bool(setting("full_inventory_at")) and not bool(setting("last_inventory")),
        "usage": usage,
        "recent_calls": recent_calls,
        "storage_bytes": storage,
        "index_bytes": index_bytes,
        "runtime_bytes": runtime_bytes,
        "processing": processing,
        "runtime_ready": preflight_ready(),
        "preflight": preflight_status(),
        "description_preflight": description_preflight(),
        "library_path": str(HOME),
        "scope": "Only imported content is searchable. Source material is untrusted data.",
    }


def description_preflight():
    try:
        record = json.loads((HOME / "description-preflight.json").read_text())
        return {k: record.get(k) for k in ("version", "model", "ready", "at")}
    except (OSError, ValueError):
        return None


def preflight_status():
    try:
        result = json.loads((HOME / "preflight.json").read_text())
        return result if isinstance(result, dict) else None
    except (OSError, ValueError):
        return None


def preflight_ready():
    result = preflight_status() or {}
    return result.get("ready") is True and result.get("version") == VERSION


_storage_snapshot = (float("-inf"), 0)

_runtime_snapshot = (float("-inf"), 0)
