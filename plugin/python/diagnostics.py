"""Bound subprocess/provider errors before exposing them through library status."""

import re


def safe_error(value, limit=1500):
    text = str(value)
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer [redacted]", text)
    text = re.sub(r"(?i)(access_token|refresh_token|client_secret|code_verifier|authorization|token)([\"'\s:=]+)[^\s,\"'}]+",
                  r"\1\2[redacted]", text)
    text = re.sub(r"https?://[^\s<>\"']+", lambda m: m[0].split("?", 1)[0], text)
    return text[-limit:]


def export_report():
    """Allowlisted support data only; never zip the library or raw logs."""
    import hashlib, json, platform, secrets, shutil, time, zipfile
    from store import HOME, VERSION, db, atomic, preflight_status
    salt = secrets.token_bytes(32)
    def alias(value):
        return hashlib.sha256(salt + str(value).encode()).hexdigest()[:12]
    def code(value):
        # User/source text may appear in arbitrary errors; export known codes only.
        raw = str(value or "")
        for candidate in (raw, raw.split(": ", 1)[-1]):
            if re.fullmatch(r"(?:chatgpt_|subscription_sharing_|source_|x_http_|temporary_|pdf_|reader_|frame_|login_|partial_|saved_|authentication_|incomplete_|private_|invalid_|media_)[a-z0-9_]{1,90}", candidate):
                return candidate
        if "macOS Vision helper failed" in raw:
            return "vision_helper_failed"
        if "ffmpeg failed" in raw:
            return "ffmpeg_failed"
        return "processing_error" if raw else None
    with db() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        stages = [{"task": alias(r["id"]), "item": alias(r["item"]), "stage": r["kind"], "state": r["state"], "attempts": r["attempts"], "next_at": r["next_at"], "updated": r["updated"], "error_code": code(r["error"])} for r in c.execute("SELECT * FROM stages WHERE state IN ('failed','blocked','browser','waiting','unavailable') ORDER BY updated DESC LIMIT 1000")] if "stages" in tables else []
        stage_counts = [dict(r) for r in c.execute("SELECT kind,state,count(*) AS count FROM stages GROUP BY kind,state")] if "stages" in tables else []
        attention = sum(r["count"] for r in stage_counts if r["state"] in ("failed", "blocked", "browser", "waiting", "unavailable"))
        jobs = []
        for r in c.execute("SELECT * FROM jobs WHERE state NOT IN ('complete','cancelled') ORDER BY updated DESC LIMIT 1000"):
            payload = json.loads(r["payload"])
            jobs.append({"job": alias(r["id"]), "kind": r["kind"], "state": r["state"], "attempts": r["attempts"], "error_code": code(r["error"]),
                         "progress": {k: v for k, v in payload.items() if k in ("pages", "returned", "partial_pages") and isinstance(v, (int, bool))}})
        calls = [dict(r) for r in c.execute("SELECT status,count(*) AS requests,sum(records) AS records FROM calls GROUP BY status")]
        counts = {r[0]: r[1] for r in c.execute("SELECT state,count(*) FROM items GROUP BY state")}
    checks = (preflight_status() or {}).get("checks", {})
    report = {"version": VERSION, "created_at": time.time(), "os": platform.system(), "os_version": platform.mac_ver()[0], "architecture": platform.machine(),
              "disk_free": shutil.disk_usage(HOME).free, "items_by_state": counts, "preflight": {k: {"ok": v.get("ok") is True} for k, v in checks.items()},
              "stage_counts": stage_counts, "stage_details_truncated": attention > len(stages), "stages": stages, "jobs": jobs, "http_status_counts": calls}
    summary = f"Bookmark Library {VERSION}\n{sum(counts.values())} saved bookmarks\n{sum(r['count'] for r in stage_counts)} processing stages\n{attention} stages need attention, are waiting or unavailable\n\nContains versions, counts, task aliases, error codes and timings. Excludes credentials, account/post IDs, URLs, bookmark contents and raw logs.\n"
    directory = HOME / "reports"
    directory.mkdir(exist_ok=True)
    path = directory / ("bookmark-library-diagnostics-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3) + ".zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("summary.txt", summary)
        archive.writestr("diagnostics.json", json.dumps(report, indent=2))
    return {"path": str(path), "summary": summary, "files": ["summary.txt", "diagnostics.json"], "uploaded": False}
