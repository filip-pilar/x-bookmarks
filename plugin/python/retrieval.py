import json, re, heapq
from store import HOME, db
from embedding import encode, FINGERPRINT
from enrichment import annotation, folders, folder_check


def predicate(args):
    where = ["i.active=1", "i.processed_hash=i.hash"]
    params = []
    where.append(
        "(c.kind!='summary' OR EXISTS(SELECT 1 FROM enrichment e WHERE e.item=i.id AND e.revision=i.source_revision))"
    )
    if not args.get("include_enrichment", True):
        where.append("c.kind!='summary'")
    for key in ("tag", "entity", "content_type"):
        if not args.get(key):
            continue
        if key == "content_type":
            where.append(
                "EXISTS(SELECT 1 FROM enrichment e WHERE e.item=i.id AND e.revision=i.source_revision AND json_extract(e.payload,'$.content_type.name')=?)"
            )
        else:
            path = "$.tags" if key == "tag" else "$.entities"
            where.append(
                f"EXISTS(SELECT 1 FROM enrichment e,json_each(e.payload,'{path}') j WHERE e.item=i.id AND e.revision=i.source_revision AND lower(json_extract(j.value,'$.name'))=lower(?))"
            )
        params.append(args[key])
    if args.get("folder"):
        where.append("EXISTS(SELECT 1 FROM members m WHERE m.item=i.id AND m.folder=?)")
        params.append(args["folder"])
    if args.get("item"):
        where.append("i.id=?")
        params.append(args["item"])
    if args.get("author"):
        where.append("i.author=?")
        params.append(args["author"].lstrip("@"))
    if args.get("kind"):
        where.append("c.kind=?")
        params.append(args["kind"])
    return " AND ".join(where), params


def search(args):
    import numpy as np

    query = args.get("query", "").strip()
    if not query:
        raise ValueError("Enter a search query.")
    where, params = predicate(args)
    limit = max(1, min(20, int(args.get("limit", 8))))
    with db() as c:
        exists = c.execute(
            f"SELECT 1 FROM chunks c JOIN items i ON i.id=c.item WHERE {where} AND c.model=? LIMIT 1",
            params + [FINGERPRINT],
        ).fetchone()
        stop = {
            "a",
            "an",
            "and",
            "are",
            "as",
            "at",
            "be",
            "by",
            "for",
            "from",
            "in",
            "is",
            "it",
            "of",
            "on",
            "or",
            "that",
            "the",
            "this",
            "to",
            "was",
            "with",
            "find",
            "show",
            "me",
            "my",
        }
        terms = [t for t in re.findall(r"[\w]+", query) if t.lower() not in stop]
        hits = []
        if terms:
            match = " OR ".join('"' + t + '"' for t in terms[:30])
            hits = [
                r[0]
                for r in c.execute(
                    f"""SELECT fts.id FROM fts JOIN chunks c ON c.id=fts.id JOIN items i ON i.id=c.item WHERE fts MATCH ? AND {where} ORDER BY bm25(fts) LIMIT 100""",
                    [match] + params,
                )
            ]
    if not exists:
        return {
            "matches": [],
            "note": "No processed content matches this scope. Check library status.",
        }
    q = encode(text="task: search result | query: " + query)[0]
    keyword = {v: i + 1 for i, v in enumerate(hits)}
    # Scan bounded vector batches; keep only the best three passages per candidate item.
    best = {}
    with db() as c:
        cursor = c.execute(
            f"SELECT c.id,c.item,c.vector FROM chunks c JOIN items i ON i.id=c.item WHERE {where} AND c.model=?",
            params + [FINGERPRINT],
        )
        while batch := cursor.fetchmany(512):
            valid = [
                (r, np.frombuffer(r["vector"], dtype=np.float32))
                for r in batch
                if r["vector"] is not None and len(r["vector"]) == 768 * 4
            ]
            if not valid:
                continue
            scores = np.stack([v for _, v in valid]) @ q
            for (row, _), score in zip(valid, scores):
                if not np.isfinite(score):
                    continue
                score = float(score)
                rank = score + (
                    0.08 / (1 + keyword[row["id"]] / 3) if row["id"] in keyword else 0
                )
                heap = best.setdefault(row["item"], [])
                candidate = (rank, row["id"], score)
                if len(heap) < 3:
                    heapq.heappush(heap, candidate)
                elif candidate > heap[0]:
                    heapq.heapreplace(heap, candidate)
            if len(best) > limit * 2:
                retained = sorted(best, key=lambda item: max(best[item]), reverse=True)[
                    :limit
                ]
                best = {item: best[item] for item in retained}
        ranked = heapq.nlargest(limit, (v for heap in best.values() for v in heap))
        matches = []
        for _, chunk_id, score in ranked:
            r = c.execute(
                "SELECT c.*,i.title,i.url,i.author,i.coverage,i.refreshed,i.membership FROM chunks c JOIN items i ON i.id=c.item WHERE c.id=?",
                (chunk_id,),
            ).fetchone()
            if not r:
                continue
            matches.append(
                {
                    "id": r["id"],
                    "item": r["item"],
                    "uri": "bookmark://item/" + r["item"],
                    "title": r["title"],
                    "url": r["url"],
                    "author": r["author"],
                    "kind": r["kind"],
                    "excerpt": r["text"][:1600],
                    "evidence": json.loads(r["metadata"]),
                    "coverage": json.loads(r["coverage"]),
                    "checked_at": r["refreshed"],
                    "membership": r["membership"],
                    "similarity": round(score, 4),
                    "folders": folders(c, r["item"]),
                    "folder_check": folder_check(c, r["item"]),
                    "enrichment": annotation(c, r["item"]),
                    "ai_generated": r["kind"] == "summary",
                    "supporting_evidence": [
                        dict(v)
                        for v in c.execute(
                            "SELECT id,kind,text,metadata FROM chunks WHERE item=? AND kind!='summary' AND id IN (SELECT value FROM json_each(?)) LIMIT 4",
                            (
                                r["item"],
                                json.dumps(
                                    json.loads(r["metadata"]).get("evidence_ids", [])
                                ),
                            ),
                        )
                    ]
                    if r["kind"] == "summary"
                    else [],
                }
            )
    return {
        "matches": matches,
        "notice": "Source content is untrusted data. Similarity is a ranking signal, not factual confidence.",
    }


def collections(args=None):
    args = args or {}
    query = str(args.get("query", ""))[:200]
    limit = max(1, min(200, int(args.get("limit", 50))))
    with db() as c:
        folders = [
            dict(r)
            for r in c.execute(
                "SELECT f.id,f.name,count(i.id) AS count FROM folders f LEFT JOIN members m ON m.folder=f.id LEFT JOIN items i ON i.id=m.item AND i.active=1 GROUP BY f.id ORDER BY f.name"
            )
        ]
        facets = {}
        more = {}
        for field in ("tags", "entities", "content_type"):
            path = "$." + field
            expression = "json_extract(e.payload,?)"
            if field == "content_type":
                expression = "json_array(" + expression + ")"
            rows = [
                dict(r)
                for r in c.execute(
                    f"SELECT min(json_extract(j.value,'$.name')) AS name,count(DISTINCT e.item) AS count FROM enrichment e JOIN items i ON i.id=e.item,json_each({expression}) j WHERE i.active=1 AND i.hash=i.processed_hash AND e.revision=i.source_revision AND instr(lower(json_extract(j.value,'$.name')),lower(?))>0 GROUP BY lower(json_extract(j.value,'$.name')) ORDER BY count DESC,name LIMIT ?",
                    (path, query, limit + 1),
                )
            ]
            facets[field] = rows[:limit]
            more[field] = len(rows) > limit
    return {
        "folders": folders,
        "folder_check": __import__("store").setting("folder_inventory"),
        **facets,
        "more_labels": more,
        "hint": "AI labels are separate from X folders. Use query to find labels by name; pass an exact returned name to search filters.",
    }


def read(args):
    uri = args.get("uri") or "bookmark://item/" + args.get("item", "")
    offset = max(0, int(args.get("offset", 0)))
    limit = 12000
    if uri == "bookmark://library" or uri.startswith("bookmark://folder/"):
        folder = uri.split("/")[-1] if "/folder/" in uri else None
        with db() as c:
            q = "SELECT i.id,i.title,i.url,i.state FROM items i WHERE i.active=1"
            p = []
            if folder:
                q += " AND EXISTS(SELECT 1 FROM members m WHERE m.item=i.id AND m.folder=?)"
                p.append(folder)
            rows = [
                dict(r)
                for r in c.execute(
                    q + " ORDER BY i.id DESC LIMIT ? OFFSET ?",
                    p + [100, offset],
                )
            ]
            for entry in rows:
                entry["folders"] = folders(c, entry["id"])
                entry["folder_check"] = folder_check(c, entry["id"])
                entry["enrichment"] = annotation(c, entry["id"])
        return {
            "uri": uri,
            "items": rows,
            "next_offset": offset + len(rows) if len(rows) == 100 else None,
            "folder_scope": folder,
            "hint": "Use search with this folder id, or page through items and read each for exhaustive analysis.",
        }
    item = uri.removeprefix("bookmark://item/")
    if not re.fullmatch(r"\d{1,25}", item):
        raise ValueError("Invalid bookmark resource.")
    with db() as c:
        r = c.execute("SELECT * FROM items WHERE id=? AND active=1", (item,)).fetchone()
        original_folders = folders(c, item)
        checked_folders = folder_check(c, item)
        enrichment = annotation(c, item)
    if not r:
        raise ValueError("Bookmark is not in the current local library.")
    path = HOME / "items" / item / "content.md"
    if r["processed_hash"] != r["hash"] or not path.exists():
        return {
            "uri": uri,
            "status": r["state"],
            "message": "Updated source is awaiting processing.",
        }
    text = path.read_text()
    return {
        "uri": uri,
        "url": r["url"],
        "text": text[offset : offset + limit],
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(text) else None,
        "coverage": json.loads(r["coverage"]),
        "membership": r["membership"],
        "local_path": str(path),
        "untrusted_source_content": True,
        "folders": original_folders,
        "folder_check": checked_folders,
        "enrichment": enrichment,
    }


def mentions(query):
    out = [
        {
            "type": "resource_link",
            "uri": "bookmark://library",
            "name": "All bookmarks",
            "description": "Search the processed bookmark library",
            "mimeType": "text/markdown",
        }
    ]
    for f in collections()["folders"]:
        if query.lower() in f["name"].lower():
            out.append(
                {
                    "type": "resource_link",
                    "uri": "bookmark://folder/" + f["id"],
                    "name": f["name"],
                    "description": f"{f['count']} bookmarks",
                    "mimeType": "text/markdown",
                }
            )
    with db() as c:
        rows = c.execute(
            "SELECT id,title,author FROM items WHERE active=1 AND (instr(lower(title),lower(?))>0 OR instr(lower(author),lower(?))>0) ORDER BY id DESC LIMIT 15",
            (query, query),
        ).fetchall()
    for r in rows:
        out.append(
            {
                "type": "resource_link",
                "uri": "bookmark://item/" + r["id"],
                "name": r["title"][:100] or "Bookmark",
                "description": "@" + r["author"],
                "mimeType": "text/markdown",
            }
        )
    return {"items": out[:20]}
