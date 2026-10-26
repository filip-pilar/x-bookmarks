"""Checkpoint parent and author-thread capture after each successful request."""

import re
from urllib.parse import urlsplit
from store import atomic, fingerprint, coverage_kind
from sources import cached_json, context_request, page_data, unavailable_posts, POLICY
from xapi import FIELDS, XError


def links(post):
    entities = post.get("note_tweet", {}).get("entities") or post.get("entities", {})
    return list(
        dict.fromkeys(
            u.get("unwound_url") or u.get("expanded_url")
            for u in entities.get("urls", [])
            if u.get("unwound_url") or u.get("expanded_url")
        )
    )


def references(post, links_only=False):
    ids = [] if links_only else [r["id"] for r in post.get("referenced_tweets", [])]
    for url in links(post):
        parsed = urlsplit(url)
        match = re.search(r"/(?:status|statuses)/(\d+)", parsed.path)
        if (
            parsed.hostname in ("x.com", "www.x.com", "twitter.com", "www.twitter.com")
            and match
        ):
            ids.append(match[1])
    return list(dict.fromkeys(ids))


def capture(post, directory, get, progress, retry=False, source_hash=None):
    path = directory / "context.json"
    old = cached_json(path)
    mode, generation = POLICY.get()
    identity = fingerprint(
        [post.get("conversation_id"), post.get("author_id"), references(post)]
    )
    # Existing v0.2.2 libraries have a complete cache without request checkpoints.
    legacy = old and "identity" not in old
    matches = old and (
        (legacy and old.get("source_hash") == source_hash)
        or old.get("identity") == identity
    )
    if (
        mode != "rebuild"
        and old
        and any(coverage_kind(w) == "sample" for w in old.get("warnings", []))
    ):
        matches = False
    if matches and mode == "rebuild":
        return old["posts"], old.get("media", []), old.get("warnings", [])
    if mode == "rebuild":
        return [], [], ["Saved source missing: thread context. Use Repair to fetch it."]
    if matches and (mode != "refresh" or old.get("generation") == generation):
        if (
            old.get("complete", legacy)
            and old.get("links_done")
            and not (
                retry
                and any(
                    coverage_kind(w) == "retryable" for w in old.get("warnings", [])
                )
            )
        ):
            return old["posts"], old.get("media", []), old.get("warnings", [])
        state = old if not legacy else None
    else:
        state = None
    if not state:
        state = {
            "identity": identity,
            "generation": generation,
            "posts": [],
            "media": [],
            "warnings": [],
            "queue": references(post),
            "visited": [post["id"]],
            "cursor": None,
            "cursors": [],
            "pages": 0,
            "parents_done": False,
            "complete": False,
        }
    state["warnings"] = [
        w for w in state["warnings"] if coverage_kind(w) != "retryable"
    ]
    while state["queue"] and len(state["visited"]) < 25 and not state["parents_done"]:
        ref = state["queue"][0]
        if ref in state["visited"]:
            state["queue"].pop(0)
            continue
        progress("context", "Quoted and parent posts")
        try:
            payload = context_request("/tweets/" + ref, FIELDS, get)
            data = payload.get("data")
            if data is None and unavailable_posts(payload, {ref}) == {ref}:
                state["warnings"].append(f"Context {ref}: source unavailable.")
            elif (
                not isinstance(data, dict)
                or data.get("id") != ref
                or payload.get("errors")
            ):
                raise RuntimeError("Incomplete related-post response.")
            else:
                state["posts"].append(data)
                state["media"].extend(payload.get("includes", {}).get("media", []))
                state["queue"].extend(references(data))
        except XError as e:
            if e.status not in (403, 404, 410):
                raise
            state["warnings"].append(
                f"Context {ref}: HTTP {e.status}; source unavailable."
            )
        state["visited"].append(ref)
        state["queue"].pop(0)
        atomic(path, state)
    if state["queue"] and not state["parents_done"]:
        state["warnings"].append("Parent/quote expansion stopped at 24 context posts.")
    state["parents_done"] = True
    conversation = post.get("conversation_id")
    if conversation:
        root = next((p for p in state["posts"] if p["id"] == conversation), post)
        query = f"conversation_id:{conversation} from:{root.get('author_id', post.get('author_id'))}"
        while state["pages"] < 3 and not state.get("thread_done"):
            args = {**FIELDS, "query": query, "max_results": 100}
            if state["cursor"]:
                args["next_token"] = state["cursor"]
            progress("context", "Author’s thread")
            payload = context_request("/tweets/search/all", args, get)
            data, nxt = page_data(payload, state["cursor"], state["cursors"])
            if payload.get("errors"):
                raise RuntimeError(
                    "Incomplete author-thread response; completed pages retained."
                )
            state["posts"].extend(p for p in data if p["id"] not in state["visited"])
            state["visited"].extend(p["id"] for p in data)
            state["media"].extend(payload.get("includes", {}).get("media", []))
            if state["cursor"]:
                state["cursors"].append(state["cursor"])
            state["cursor"] = nxt
            state["pages"] += 1
            state["thread_done"] = not nxt
            atomic(path, state)
        if state["cursor"]:
            state["warnings"].append(
                "Author-thread expansion stopped after 300 returned posts."
            )
    # Follow explicit post links discovered inside the author thread. Reuse its
    # checkpoint so this upgrade does not fetch the thread pages again.
    if not state.get("links_done"):
        state.setdefault(
            "linked_queue",
            list(
                dict.fromkeys(
                    ref
                    for p in [post, *state["posts"]]
                    for ref in references(p, links_only=True)
                    if ref not in state["visited"]
                )
            ),
        )
        state.setdefault("linked_count", 0)
        while state["linked_queue"] and state["linked_count"] < 24:
            ref = state["linked_queue"][0]
            if ref in state["visited"]:
                state["linked_queue"].pop(0)
                continue
            progress("context", "Posts linked from the author’s thread")
            try:
                payload = context_request("/tweets/" + ref, FIELDS, get)
                data = payload.get("data")
                if data is None and unavailable_posts(payload, {ref}) == {ref}:
                    state["warnings"].append(f"Context {ref}: source unavailable.")
                elif (
                    not isinstance(data, dict)
                    or data.get("id") != ref
                    or payload.get("errors")
                ):
                    raise RuntimeError("Incomplete linked-post response.")
                else:
                    state["posts"].append(data)
                    state["media"].extend(payload.get("includes", {}).get("media", []))
                    state["linked_queue"].extend(references(data))
            except XError as e:
                if e.status not in (403, 404, 410):
                    raise
                state["warnings"].append(
                    f"Context {ref}: HTTP {e.status}; source unavailable."
                )
            state["visited"].append(ref)
            state["linked_count"] += 1
            state["linked_queue"].pop(0)
            atomic(path, state)
        if state["linked_queue"]:
            state["warnings"].append(
                "Linked-post expansion stopped at 24 context posts."
            )
        state["links_done"] = True
    state["complete"] = True
    state["warnings"] = list(dict.fromkeys(state["warnings"]))
    atomic(path, state)
    return state["posts"], state["media"], state["warnings"]
