"""Source reuse policies shared by fetching and local processing."""

import contextlib, contextvars, hashlib, json, shutil
from store import HOME, atomic, fingerprint

POLICY = contextvars.ContextVar("source_policy", default=("repair", ""))
RESERVE = 2 * 1024**3


class DiskSpaceError(RuntimeError):
    pass


def reserve_space(size=0, location=HOME):
    if shutil.disk_usage(location).free < RESERVE + size:
        raise DiskSpaceError(
            "Not enough free space for this step plus a 2 GB reserve. Free space, then resume."
        )


@contextlib.contextmanager
def policy(mode, generation=""):
    if mode not in ("repair", "rebuild", "refresh"):
        raise ValueError("Unknown processing mode.")
    token = POLICY.set((mode, generation))
    try:
        yield
    finally:
        POLICY.reset(token)


def checksum(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def cached_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def reusable(record):
    mode, generation = POLICY.get()
    return mode != "refresh" or (record or {}).get("generation") == generation


def context_request(path, params, get):
    from store import db
    from xapi import FIELDS
    import re

    match = re.fullmatch(r"/tweets/([0-9]{1,25})", path)
    if match and params == FIELDS and POLICY.get()[0] != "refresh":
        with db() as c:
            row = c.execute(
                "SELECT raw FROM items WHERE id=? AND active=1", (match[1],)
            ).fetchone()
        if row:
            raw = json.loads(row[0])
            return {"data": raw["post"], "includes": raw["includes"]}
    key = fingerprint([path, params])
    file = HOME / "sources/x" / (key + ".json")
    saved = cached_json(file)
    if saved and reusable(saved):
        return saved["response"]
    if POLICY.get()[0] == "rebuild":
        raise RuntimeError("Saved source missing; use Repair to fetch it.")
    result = get(path, params)
    if not isinstance(result, dict) or not any(
        k in result for k in ("data", "meta", "errors")
    ):
        raise RuntimeError("X returned an invalid source response; progress retained.")
    # A partial response can be retried; do not make it a permanent cache hit.
    if not result.get("errors"):
        atomic(file, {"response": result, "generation": POLICY.get()[1]})
    return result


def page_data(page, cursor=None, seen=(), *, folder=False):
    if not isinstance(page, dict):
        raise RuntimeError("Invalid X page; previous inventory retained.")
    data, meta = page.get("data"), page.get("meta", {})
    # The folder endpoint omits both optional fields when the account has no folders.
    # This exception does not apply to bookmark inventory or search responses.
    if folder and page == {} and cursor is None:
        data = []
    if not isinstance(meta, dict) or not isinstance(page.get("errors", []), list):
        raise RuntimeError("Invalid X page metadata; previous inventory retained.")
    if data is None and meta.get("result_count") == 0:
        data = []
    if not isinstance(data, list) or any(
        not isinstance(r, dict) or not str(r.get("id", "")).isdigit() for r in data
    ):
        raise RuntimeError("Invalid X page records; previous inventory retained.")
    if folder and any(not isinstance(r.get("name"), str) for r in data):
        raise RuntimeError("Invalid X folder records; previous inventory retained.")
    if "result_count" in meta and meta["result_count"] != len(data):
        raise RuntimeError(
            "X page count does not match its records; previous inventory retained."
        )
    nxt = meta.get("next_token")
    if nxt is not None and (
        not isinstance(nxt, str) or not nxt or nxt == cursor or nxt in seen
    ):
        raise RuntimeError(
            "X repeated or invalidated a page cursor; progress retained."
        )
    return data, nxt


def unavailable_posts(page, requested):
    """Recognize only explicit per-post absence; access/billing errors must stop work."""
    errors = page.get("errors", [])
    if not isinstance(errors, list):
        return None
    unavailable = set()
    for error in errors:
        if (
            not isinstance(error, dict)
            or error.get("resource_type") != "tweet"
            or error.get("resource_id") not in requested
            or str(error.get("type", "")).rsplit("/", 1)[-1]
            not in ("resource-not-found", "not-authorized-for-resource")
        ):
            return None
        unavailable.add(error["resource_id"])
    return unavailable
