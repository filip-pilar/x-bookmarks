import json, threading, time
from store import (
    HOME,
    db,
    setting,
    set_setting,
    config,
    enqueue,
    upsert_page,
    coverage_kind,
    preflight_ready,
)
from xapi import get, FIELDS, RetryLater, XError

WAKE = threading.Event()
FOLDER_INTERVAL = 24 * 60 * 60


def require_runtime():
    if not preflight_ready():
        raise RuntimeError("Run setup for this plugin version and pass the local processing preflight before resuming work.")


def resume_sync(c, mode):
    active = c.execute(
        "SELECT * FROM jobs WHERE kind='sync' AND state IN ('queued','running','waiting','failed') ORDER BY created DESC LIMIT 1"
    ).fetchone()
    if active:
        actual = json.loads(active["payload"]).get("mode", "full")
        if actual != mode:
            raise RuntimeError(
                f"An unfinished {actual} sync exists. Resume or retry it before starting a {mode} sync."
            )
        c.execute(
            "UPDATE jobs SET state='queued',attempts=0,error=NULL,next_at=0 WHERE id=? AND state='failed'",
            (active["id"],),
        )
        return active["id"]
    return None


def sync_folders():
    require_runtime()
    if not config().get("account"):
        raise RuntimeError("Connect X first.")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = resume_sync(c, "folders")
        ids = [r[0] for r in c.execute("SELECT id FROM items WHERE active=1")]
        job = existing or enqueue(
            c,
            "sync",
            "",
            {
                "mode": "folders",
                "phase": "folders",
                "pages": 0,
                "returned": 0,
                "cursor": None,
                "cursors": [],
                "existing_ids": ids,
                "check_folders": True,
            },
        )
    set_setting("paused", False)
    WAKE.set()
    return {
        "job_id": job,
        "scope": "folders for existing bookmarks only",
        "items": len(ids),
        "imports_bookmarks": False,
        "resumed": bool(existing),
    }


def sync(mode="new"):
    require_runtime()
    if mode not in ("new", "full"):
        raise ValueError("Sync mode must be new or full.")
    if not config().get("account"):
        raise RuntimeError("Connect X first.")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        if not setting("full_inventory_at"):
            mode = "full"
        existing = resume_sync(c, mode)
        folders = setting("folder_inventory", {}) or {}
        check_folders = (
            mode == "full"
            or not folders.get("complete")
            or time.time() - folders.get("at", 0) >= FOLDER_INTERVAL
        )
        job = existing or enqueue(
            c, "sync", "", {
                "mode": mode,
                "phase": "bookmarks",
                "pages": 0,
                "returned": 0,
                "cursor": None,
                "cursors": [],
                "page_size": 10 if mode == "new" else 100,
                "check_folders": check_folders,
            },
        )
    set_setting("paused", False)
    WAKE.set()
    return {"job_id": job, "mode": mode, "started": True, "resumed": bool(existing)}


def checkpoint(c, job, payload):
    c.execute(
        "UPDATE jobs SET payload=?,state='queued',updated=? WHERE id=?",
        (json.dumps(payload), time.time(), job),
    )


def scan_page(run, path, args, cursor=None, seen=(), folder=False, lookup_ids=None):
    from store import atomic, fingerprint
    from sources import cached_json, page_data, unavailable_posts

    file = HOME / "scans" / run / (fingerprint([path, args]) + ".json")
    page = cached_json(file)
    if page is None:
        page = get(path, args)
    unavailable = None
    if lookup_ids:
        unavailable = unavailable_posts(page, lookup_ids)
        if "data" not in page and unavailable == set(lookup_ids):
            page = {**page, "data": []}
    data, nxt = page_data(page, cursor, seen, folder=folder)
    if lookup_ids:
        returned = {r["id"] for r in data}
        if returned - set(lookup_ids) or (returned & (unavailable or set())):
            raise RuntimeError("Incomplete folder-post lookup; progress retained.")
        if returned | (unavailable or set()) != set(lookup_ids) and not page.get("errors"):
            raise RuntimeError("Incomplete folder-post lookup without per-record errors; progress retained.")
    atomic(file, page)
    return page, data, nxt


def page_warnings(p, page, phase):
    if page.get("errors"):
        p["partial_pages"] = p.get("partial_pages", 0) + 1
        if phase != "bookmarks":
            p["folder_gaps"] = True
        # Full private responses remain in scans; status contains only counts and phases.
        p.setdefault("api_warnings", {})[phase] = p.get("api_warnings", {}).get(phase, 0) + len(page["errors"])


def advance(p, nxt):
    if p.get("cursor"):
        p.setdefault("cursors", []).append(p["cursor"])
    p["cursor"] = nxt


def sync_step(job):
    from maintenance import inventory_snapshot

    p = json.loads(job["payload"])
    owner, run = config()["account"]["id"], job["id"]
    inventory_snapshot(HOME / "scans" / run / "before-reconciliation.json")
    phase, cursor = p.get("phase", "bookmarks"), p.get("cursor")
    args = {"max_results": p.get("page_size", 100) if phase == "bookmarks" else 100}
    if cursor:
        args["pagination_token"] = cursor
    if phase == "bookmarks":
        page, data, nxt = scan_page(
            run,
            f"/users/{owner}/bookmarks",
            {**FIELDS, **args},
            cursor,
            p.get("cursors", []),
        )
        page_warnings(p, page, "bookmarks")
        with db() as c:
            known = bool(data) and all(
                c.execute(
                    "SELECT 1 FROM items WHERE id=? AND active=1 AND seen_run IS NOT NULL AND seen_run!=?",
                    (r["id"], run),
                ).fetchone()
                for r in data
            )
            upsert_page(c, {**page, "data": data}, run)
            advance(p, nxt)
            p.update(
                pages=p.get("pages", 0) + 1, returned=p.get("returned", 0) + len(data)
            )
            if not nxt or (p.get("mode") == "new" and known):
                p.update(
                    phase="folders" if p.get("check_folders", True) else "finish",
                    cursor=None,
                    cursors=[],
                    endpoint_exhausted=not bool(nxt),
                )
            checkpoint(c, run, p)
        return
    if phase == "finish":
        finish_sync(run, p)
        return
    if phase == "folders":
        page, data, nxt = scan_page(
            run,
            f"/users/{owner}/bookmarks/folders",
            args,
            cursor,
            p.get("cursors", []),
            True,
        )
        page_warnings(p, page, "folders")
        with db() as c:
            for folder in data:
                c.execute(
                    "INSERT OR REPLACE INTO scan_folders VALUES (?,?,?)",
                    (run, folder["id"], folder["name"]),
                )
                c.execute("INSERT OR REPLACE INTO folders VALUES (?,?,?)",
                          (folder["id"], folder["name"], run))
            c.execute("INSERT OR REPLACE INTO settings VALUES ('folder_inventory',?)",
                      (json.dumps({"complete": False, "in_progress": True, "run": run}),))
            advance(p, nxt)
            if not nxt:
                p.update(
                    phase="members",
                    folder_ids=[
                        r[0]
                        for r in c.execute(
                            "SELECT id FROM scan_folders WHERE run=? ORDER BY id",
                            (run,),
                        )
                    ],
                    folder_index=0,
                    cursors=[],
                )
            checkpoint(c, run, p)
        return
    folders, idx = p.get("folder_ids", []), p.get("folder_index", 0)
    if phase == "members" and idx < len(folders):
        folder = folders[idx]
        page, data, nxt = scan_page(
            run,
            f"/users/{owner}/bookmarks/folders/{folder}",
            args,
            cursor,
            p.get("cursors", []),
        )
        page_warnings(p, page, "members")
        with db() as c:
            for item in data:
                if p.get("mode") == "folders" and item["id"] not in p["existing_ids"]:
                    continue
                c.execute(
                    "INSERT OR REPLACE INTO scan_members VALUES (?,?,?)",
                    (run, folder, item["id"]),
                )
                c.execute("INSERT OR REPLACE INTO members VALUES (?,?,?)",
                          (folder, item["id"], run))
            advance(p, nxt)
            if not nxt:
                p.update(folder_index=idx + 1, cursors=[])
            checkpoint(c, run, p)
        return
    # Resolve folder-only IDs before publishing memberships. Persist each batch.
    skipped = p.get("unresolved_ids", [])
    with db() as c:
        missing = [
            r[0]
            for r in c.execute(
                "SELECT DISTINCT m.item FROM scan_members m LEFT JOIN items i ON i.id=m.item AND i.active=1 WHERE m.run=? AND i.id IS NULL ORDER BY m.item",
                (run,),
            )
            if r[0] not in skipped
        ][:100]
    if missing:
        page, data, _ = scan_page(
            run, "/tweets", {**FIELDS, "ids": ",".join(missing)}, lookup_ids=missing
        )
        page_warnings(p, page, "hydrate")
        returned = {r["id"] for r in data}
        p["unresolved_ids"] = skipped + [i for i in missing if i not in returned]
        with db() as c:
            upsert_page(c, {**page, "data": data}, run)
            p["phase"] = "hydrate"
            checkpoint(c, run, p)
        return
    finish_sync(run, p)


def finish_sync(run, p):
    # Retain the previous inventory; source files are never deleted by a scan.
    from maintenance import inventory_snapshot

    inventory_snapshot(HOME / "scans" / run / "before-reconciliation.json")
    checked = p.get("check_folders", True)
    now = time.time()
    with db() as c:
        folders_complete = checked and not p.get("folder_gaps") and not p.get("unresolved_ids")
        if checked:
            if folders_complete:
                c.execute("DELETE FROM members")
                c.execute("DELETE FROM folders")
            c.execute(
                "INSERT OR REPLACE INTO folders SELECT id,name,run FROM scan_folders WHERE run=?", (run,)
            )
            c.execute(
                "INSERT OR REPLACE INTO members SELECT folder,item,run FROM scan_members WHERE run=?", (run,)
            )
            if folders_complete and p.get("mode") == "folders":
                c.executemany(
                    "UPDATE items SET folder_checked_at=? WHERE id=?",
                    [(now, i) for i in p["existing_ids"]],
                )
            elif folders_complete:
                c.execute(
                    "UPDATE items SET folder_checked_at=? WHERE active=1", (now,)
                )
        full = (
            p.get("mode", "full") == "full"
            and p.get("endpoint_exhausted")
            and not p.get("unavailable_records")
            and not p.get("partial_pages")
        )
        if full:
            c.execute(
                "UPDATE items SET membership=CASE WHEN seen_run=? OR id IN (SELECT item FROM members) THEN 'present' ELSE 'unseen' END WHERE active=1",
                (run,),
            )
            c.execute(
                "INSERT OR REPLACE INTO settings VALUES ('full_inventory_at',?)",
                (json.dumps(time.time()),),
            )
        unresolved = p.get("unresolved_ids", [])
        if not checked:
            unresolved = (setting("folder_inventory", {}) or {}).get(
                "unresolved_ids",
                (setting("last_inventory", {}) or {}).get("unresolved_folder_ids", []),
            )
        last = {
            "at": time.time(),
            "pages": p["pages"],
            "returned": p["returned"],
            "mode": p.get("mode", "full"),
            "endpoint_exhausted": p.get("endpoint_exhausted", False),
            "folders_checked": checked,
            "folders_complete": folders_complete,
            "partial_pages": p.get("partial_pages", 0),
            "api_warnings": p.get("api_warnings", {}),
            "unavailable_records": p.get("unavailable_records", 0),
            "unresolved_folder_ids": unresolved,
            "historical_coverage": "All exposed pages only; historical completeness is not independently verified.",
            "unseen_retained": c.execute(
                "SELECT count(*) FROM items WHERE active=1 AND membership='unseen'"
            ).fetchone()[0],
        }
        if checked:
            c.execute(
                "INSERT OR REPLACE INTO settings VALUES ('folder_inventory',?)",
                (
                    json.dumps(
                        {
                            "at": now,
                            "complete": bool(folders_complete and not unresolved),
                            "in_progress": False,
                            "partial_pages": p.get("partial_pages", 0),
                            "scope": "existing" if p.get("mode") == "folders" else "all",
                            "folders": len(p.get("folder_ids", [])),
                            "unresolved_ids": unresolved,
                        }
                    ),
                ),
            )
        if p.get("mode") != "folders":
            c.execute(
                "INSERT OR REPLACE INTO settings VALUES ('last_inventory',?)",
                (json.dumps(last),),
            )
        c.execute(
            "UPDATE jobs SET state='complete',updated=?,payload=? WHERE id=?",
            (time.time(), json.dumps(p), run),
        )
        c.execute("DELETE FROM scan_members WHERE run=?", (run,))
        c.execute("DELETE FROM scan_folders WHERE run=?", (run,))


def process_saved(mode="rebuild", item=None):
    require_runtime()
    if mode not in ("rebuild", "repair", "refresh"):
        raise ValueError("Unknown processing mode.")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute(
            "SELECT 1 FROM jobs WHERE state IN ('queued','running','waiting') OR (kind='sync' AND state='failed') LIMIT 1"
        ).fetchone():
            raise RuntimeError(
                "Finish or resume the existing queue before starting another operation."
            )
        rows = c.execute(
            "SELECT id FROM items WHERE active=1" + (" AND id=?" if item else ""),
            (item,) if item else (),
        ).fetchall()
        if item and not rows:
            raise ValueError("Bookmark is not in this library.")
        for row in rows:
            enqueue(c, "process", row[0], {"mode": mode, "retry": mode == "repair"})
    set_setting("paused", False)
    WAKE.set()
    return {"queued": len(rows), "mode": mode, "network_allowed": mode != "rebuild"}


def control(action):
    if action in ("resume", "retry"):
        require_runtime()
    if action in ("pause", "resume"):
        set_setting("paused", action == "pause")
        if action == "resume":
            set_setting("pause_reason", "")
    elif action == "retry":
        with db() as c:
            c.execute(
                "UPDATE jobs AS failed SET state='queued',attempts=0,next_at=0,error=NULL,payload=CASE WHEN kind='process' THEN json_set(payload,'$.retry',1) ELSE payload END "
                "WHERE state='failed' AND NOT EXISTS (SELECT 1 FROM jobs active WHERE active.kind=failed.kind AND active.item=failed.item AND active.state IN ('queued','running','waiting'))"
            )
            for row in c.execute(
                "SELECT id,coverage FROM items WHERE active=1 AND state='partial'"
            ).fetchall():
                if any(
                    coverage_kind(w) == "retryable" for w in json.loads(row["coverage"])
                ):
                    enqueue(c, "process", row["id"], {"retry": True})
        set_setting("paused", False)
    else:
        raise ValueError("Unknown queue control.")
    WAKE.set()
    return {"action": action}


def loop(kind):
    while True:
        job = None
        if not setting("paused", False):
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                row = c.execute(
                    "SELECT * FROM jobs WHERE kind=? AND state IN ('queued','waiting') AND next_at<=? "
                    "AND (?='sync' OR NOT EXISTS(SELECT 1 FROM jobs WHERE kind='sync' AND state IN ('queued','running','waiting'))) "
                    "ORDER BY created LIMIT 1",
                    (kind, time.time(), kind),
                ).fetchone()
                if row:
                    job = dict(row)
                    c.execute(
                        "UPDATE jobs SET state='running',updated=? WHERE id=?",
                        (time.time(), job["id"]),
                    )
        if not job:
            WAKE.wait(3)
            WAKE.clear()
            continue
        try:
            if job["kind"] == "sync":
                sync_step(job)
            else:
                from processing import process

                payload = json.loads(job["payload"])
                process(
                    job["item"],
                    retry=bool(payload.get("retry")),
                    mode=payload.get("mode", "repair"),
                    generation=job["id"],
                )
                with db() as c:
                    c.execute("BEGIN IMMEDIATE")
                    c.execute(
                        "UPDATE jobs SET state='complete',error=NULL,updated=? WHERE id=?",
                        (time.time(), job["id"]),
                    )
                    current = c.execute("SELECT hash,processed_hash FROM items WHERE id=?", (job["item"],)).fetchone()
                    if current and current["hash"] != current["processed_hash"]:
                        enqueue(c, "process", job["item"], {"mode": "repair"})
                        c.execute("UPDATE items SET state='pending' WHERE id=?", (job["item"],))
        except Exception as e:
            from processing import Paused

            from sources import DiskSpaceError

            if isinstance(e, (Paused, DiskSpaceError)):
                if isinstance(e, DiskSpaceError):
                    set_setting("paused", True)
                    set_setting("pause_reason", str(e))
                with db() as c:
                    c.execute("UPDATE jobs SET state='queued' WHERE id=?", (job["id"],))
                continue
            attempts = job["attempts"] + 1
            when = (
                e.when
                if isinstance(e, RetryLater)
                else time.time() + min(300, 15 * 2**attempts)
            )
            terminal = (
                isinstance(e, XError)
                and 400 <= e.status < 500
                and e.status not in (408, 429)
            )
            state = (
                "waiting"
                if not terminal and (isinstance(e, RetryLater) or attempts < 3)
                else "failed"
            )
            if terminal and e.status in (401, 402):
                set_setting("paused", True)
                set_setting(
                    "pause_reason",
                    "X access or credits need attention. Resolve the issue, then retry failed work.",
                )
            from diagnostics import safe_error
            error = safe_error(e)
            with db() as c:
                c.execute(
                    "UPDATE jobs SET state=?,attempts=?,next_at=?,error=?,updated=? WHERE id=?",
                    (state, attempts, when, error, time.time(), job["id"]),
                )
                if job["kind"] == "process":
                    c.execute(
                        "UPDATE items SET state=? WHERE id=?", ("error", job["item"])
                    )
        finally:
            if job["kind"] == "process":
                set_setting("processing", None)
