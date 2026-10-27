"""Bounded browsing, evidence reads and deterministic metric queries."""
import base64, io, json, re
from store import HOME, db
from enrichment import folders

METRICS = ("like_count", "retweet_count", "reply_count", "quote_count", "bookmark_count", "impression_count")


def content_types(raw):
    post = raw.get("post", {})
    kinds = ["Article"] if post.get("article") else ["Post"]
    types = {m.get("type") for m in raw.get("includes", {}).get("media", [])}
    if "photo" in types:
        kinds.append("Image")
    if types & {"video", "animated_gif"}:
        kinds.append("Video")
    return kinds


def browse(args):
    limit = max(1, min(100, int(args.get("limit", 30))))
    offset = max(0, int(args.get("offset", 0)))
    query, folder = str(args.get("query", ""))[:300], args.get("folder")
    where, params = ["i.active=1"], []
    if query:
        where.append("(instr(lower(i.title),lower(?)) OR instr(lower(i.author),lower(?)) OR EXISTS(SELECT 1 FROM chunks c WHERE c.item=i.id AND instr(lower(c.text),lower(?))))")
        params += [query] * 3
    if folder:
        where.append("EXISTS(SELECT 1 FROM members m WHERE m.item=i.id AND m.folder=?)")
        params.append(folder)
    if args.get("state"):
        where.append("i.state=?")
        params.append(args["state"])
    metric = args.get("sort")
    order = f"json_extract(o.metrics,'$.{metric}') DESC NULLS LAST,i.id DESC" if metric in METRICS else "i.id DESC"
    with db() as c:
        rows = c.execute("SELECT i.id,i.title,i.author,i.url,i.state,i.updated,i.raw,o.metrics,o.at AS metrics_at FROM items i LEFT JOIN observations o ON o.source=i.id AND o.at=(SELECT max(at) FROM observations WHERE source=i.id) WHERE " + " AND ".join(where) + " ORDER BY " + order + " LIMIT ? OFFSET ?", params + [limit + 1, offset]).fetchall()
        total = c.execute("SELECT count(*) FROM items i WHERE " + " AND ".join(where), params).fetchone()[0]
        result = []
        for row in rows[:limit]:
            entry = dict(row)
            raw = json.loads(entry.pop("raw"))
            entry["text"] = __import__("pipeline").post_text(raw.get("post", {}))
            entry["folders"] = folders(c, row["id"])
            entry["metrics"] = json.loads(entry["metrics"]) if entry["metrics"] else None
            entry["has_media"] = bool(raw.get("includes", {}).get("media"))
            entry["content_types"] = content_types(raw)
            result.append(entry)
    return {"items": result, "total": total, "next_offset": offset + limit if len(rows) > limit else None}


def detail(args):
    item = str(args["item"])
    if not re.fullmatch(r"[0-9]{1,25}", item):
        raise ValueError("Invalid bookmark identifier.")
    offset = max(0, int(args.get("offset", 0)))
    with db() as c:
        row = c.execute("SELECT id,title,author,url,state,raw,coverage,membership FROM items WHERE id=?", (item,)).fetchone()
        if not row:
            raise ValueError("Bookmark is not in this library.")
        result = dict(row)
        raw = json.loads(result.pop("raw"))
        result["post"] = raw.get("post", {})
        result["content_types"] = content_types(raw)
        result["text"] = __import__("pipeline").post_text(result["post"])
        result["folders"] = folders(c, item)
        result["coverage"] = json.loads(result["coverage"])
        result["evidence"] = [{**dict(r), "metadata": json.loads(r["metadata"])} for r in c.execute("SELECT id,kind,text,metadata FROM chunks WHERE item=? ORDER BY CASE kind WHEN 'text' THEN 0 WHEN 'context' THEN 1 WHEN 'linked-document' THEN 2 WHEN 'description' THEN 3 WHEN 'transcript' THEN 4 ELSE 5 END,json_extract(metadata,'$.source_id'),coalesce(json_extract(metadata,'$.start_seconds'),json_extract(metadata,'$.start_char'),0),id LIMIT 41 OFFSET ?", (item, offset))]
        result["next_offset"] = offset + 40 if len(result["evidence"]) > 40 else None
        result["evidence"] = result["evidence"][:40]
        # A search hit can be beyond the first evidence page. Return it separately
        # without changing pagination or dropping the beginning of the saved post.
        hit = c.execute("SELECT id,kind,text,metadata FROM chunks WHERE item=? AND id=?", (item, args.get("evidence_id", ""))).fetchone()
        result["matched_evidence"] = {**dict(hit), "metadata": json.loads(hit["metadata"])} if hit else None
        result["stages"] = [dict(r) for r in c.execute("SELECT id,kind,state,source,error,next_at,run FROM stages WHERE item=? ORDER BY updated LIMIT 200", (item,))]
        result["metrics"] = [{"at": r["at"], "values": json.loads(r["metrics"])} for r in c.execute("SELECT * FROM observations WHERE source=? ORDER BY at DESC LIMIT 20", (item,))]
        for saved in c.execute("SELECT result FROM stages WHERE item=? AND state='complete'", (item,)):
            result["coverage"].extend(json.loads(saved[0]).get("coverage", []))
        result["coverage"] = list(dict.fromkeys(result["coverage"]))
        result["media"] = [{k: m[k] for k in ("media_key", "type", "alt_text", "duration_ms", "width", "height", "public_metrics") if k in m} for m in raw.get("includes", {}).get("media", [])]
        from pipeline import clearable_assets
        result["clearable_bytes"] = sum(size for _, size in clearable_assets(c, item))
        result["retryable_steps"] = c.execute("SELECT count(*) FROM stages WHERE item=? AND state IN ('failed','blocked','waiting')", (item,)).fetchone()[0] + c.execute("SELECT count(*) FROM jobs WHERE item=? AND state='failed'", (item,)).fetchone()[0]
    result["untrusted_source_content"] = True
    return result


def aggregate(args):
    metric = args.get("metric", "like_count")
    if metric not in METRICS:
        raise ValueError("Unsupported metric.")
    where, params = ["i.active=1"], []
    if args.get("folder"):
        where.append("EXISTS(SELECT 1 FROM members WHERE item=i.id AND folder=?)")
        params.append(args["folder"])
    if args.get("author"):
        where.append("i.author=?")
        params.append(args["author"].lstrip("@"))
    with db() as c:
        row = c.execute(f"SELECT count(*) AS bookmarks,count(json_extract(o.metrics,'$.{metric}')) AS measured,sum(json_extract(o.metrics,'$.{metric}')) AS total,avg(json_extract(o.metrics,'$.{metric}')) AS average,min(o.at) AS oldest_observation,max(o.at) AS newest_observation FROM items i LEFT JOIN observations o ON o.source=i.id AND o.at=(SELECT max(at) FROM observations WHERE source=i.id) WHERE " + " AND ".join(where), params).fetchone()
    return {"metric": metric, **dict(row), "missing": row["bookmarks"] - row["measured"], "scope": "Latest saved observation for each matching bookmark; no network refresh."}


def thumbnail(args):
    from PIL import Image
    item = str(args["item"])
    with db() as c:
        rows = c.execute("SELECT metadata FROM chunks WHERE item=? AND kind IN ('image','video','description') LIMIT 50", (item,)).fetchall()
    for row in rows:
        meta = json.loads(row[0])
        asset = meta.get("asset_id")
        candidates = [__import__("pipeline").asset_path(asset) / ("source-" + meta.get("sha256", "") + ".jpg")] if asset else []
        for key in ("preview", "path"):
            if meta.get(key):
                path = __import__("pathlib").Path(meta[key]).resolve()
                if path.is_relative_to(HOME.resolve()):
                    candidates.append(path)
        for path in candidates:
            if path.is_file() and path.suffix.lower() not in (".mp4", ".wav"):
                try:
                    with Image.open(path) as original:
                        image = original.convert("RGB"); image.thumbnail((480, 320))
                        out = io.BytesIO(); image.save(out, "JPEG", quality=75)
                    return {"image": "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()}
                except (OSError, ValueError):
                    continue
    return {"image": None, "note": "Original media is not cached. Open the source to view it."}
