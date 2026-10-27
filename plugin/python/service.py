import concurrent.futures, fcntl, json, os, secrets, threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from store import HOME, init, atomic, status, VERSION, preflight_ready, set_setting
import jobs, retrieval, xapi, enrichment, pipeline, subscription, library, pages, diagnostics

PORT = int(os.environ.get("BOOKMARK_LIBRARY_PORT", "8765"))
SEARCH_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1)
SEARCHES = OrderedDict()
SEARCH_LOCK = threading.Lock()


def search_async(args):
    from store import fingerprint

    key = fingerprint(args)
    with SEARCH_LOCK:
        if key not in SEARCHES:
            SEARCHES[key] = SEARCH_POOL.submit(retrieval.search, args)
        future = SEARCHES[key]
        while len(SEARCHES) > 32:
            oldest = next(iter(SEARCHES))
            if not SEARCHES[oldest].done():
                break
            SEARCHES.pop(oldest)
    try:
        result = future.result(timeout=12)
        with SEARCH_LOCK:
            SEARCHES.pop(key, None)
        return result
    except concurrent.futures.TimeoutError:
        return {
            "pending": True,
            "query_id": key,
            "message": "Local model is preparing the search. Poll status with query_id.",
            "retry_after_seconds": 5,
        }


def query_status(args):
    key = args.get("query_id")
    if not key:
        return status()
    with SEARCH_LOCK:
        future = SEARCHES.get(key)
    if future is None:
        return {
            "error": "Search expired or service restarted. Submit the search again."
        }
    if not future.done():
        return {"pending": True, "query_id": key, "retry_after_seconds": 5}
    result = future.result()
    with SEARCH_LOCK:
        SEARCHES.pop(key, None)
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def output(self, code, data, html=False):
        body = data.encode() if html else json.dumps(data).encode()
        self.send_response(code)
        self.send_header(
            "Content-Type", "text/html; charset=utf-8" if html else "application/json"
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host") not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            return self.output(403, {"error": "Invalid host"})
        parsed = urlsplit(self.path)
        if parsed.path not in ("/oauth/x/callback", "/oauth/chatgpt/callback"):
            return self.output(404, {"error": "Not found"})
        try:
            if parsed.path == "/oauth/chatgpt/callback":
                subscription.finish(parse_qs(parsed.query))
                message = "ChatGPT connected. Choose a description model in Bookmark Library settings. No import started."
            else:
                xapi.finish(parse_qs(parsed.query))
                message = "X connected. Credentials saved to your Mac Keychain. You can return to ChatGPT."
        except Exception as e:
            message = str(e)
        import html

        self.output(
            200,
            '<!doctype html><meta charset="utf-8"><title>Bookmark Library</title><main style="font:18px system-ui;max-width:540px;margin:100px auto;padding:24px"><h1>Bookmark Library</h1><p>'
            + html.escape(message)
            + "</p></main>",
            True,
        )

    def do_POST(self):
        if (
            self.headers.get("Host") != f"127.0.0.1:{PORT}"
            or self.headers.get("Origin")
            or not secrets.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + CAPABILITY
            )
        ):
            return self.output(403, {"error": "Unauthorized"})
        try:
            size = int(self.headers.get("Content-Length", 0))
            if size < 0 or size > 2_500_000:
                raise ValueError("Request too large.")
            data = json.loads(self.rfile.read(size))
            action = data["action"]
            args = data.get("args", {})
            handlers = {
                "status": lambda: query_status(args),
                "sync": lambda: jobs.sync(args.get("mode", "new")),
                "process_saved": lambda: jobs.process_saved(
                    args.get("mode", "rebuild"), args.get("item")
                ),
                "control": lambda: jobs.control(args["action"], args.get("run"), args.get("item")),
                "connect": xapi.begin,
                "disconnect": xapi.disconnect,
                "search": lambda: search_async(args),
                "read": lambda: retrieval.read(args),
                "collections": lambda: retrieval.collections(args),
                "enrichment_pending": lambda: enrichment.pending(args),
                "enrichment_evidence": lambda: enrichment.evidence(args),
                "save_enrichment": lambda: enrichment.save(args),
                "sync_folders": jobs.sync_folders,
                "mentions": lambda: retrieval.mentions(args.get("query", "")),
                "browse": lambda: library.browse(args),
                "detail": lambda: library.detail(args),
                "thumbnail": lambda: library.thumbnail(args),
                "clear_cache": lambda: pipeline.clear_cache(args.get("item")),
                "aggregate": lambda: library.aggregate(args),
                "diagnostics": diagnostics.export_report,
                "processing_details": lambda: __import__("processing").page(args),
                "browser_pending": lambda: pages.pending(args.get("limit", 20)),
                "browser_capture": lambda: pages.save(args),
                "chatgpt_connect": lambda: subscription.begin(PORT, args.get("account")),
                "chatgpt_models": lambda: {"models": subscription.models()},
                "chatgpt_select": lambda: subscription.select(args.get("model"), args.get("account")),
                "chatgpt_disconnect": subscription.disconnect,
            }
            if action not in handlers:
                raise ValueError("Unknown operation.")
            self.output(200, handlers[action]())
        except Exception as e:
            self.output(400, {"error": str(e)[:500]})


if __name__ == "__main__":
    lock = (HOME / "service.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)
    init()
    if not preflight_ready():
        set_setting("paused", True)
        set_setting("pause_reason", "Run setup to build local helpers and verify processing for this plugin version.")
    CAPABILITY = secrets.token_urlsafe(48)
    http = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    atomic(
        HOME / "service.json",
        {"pid": os.getpid(), "port": PORT, "token": CAPABILITY, "version": VERSION},
    )
    for kind in ("sync", "process"):
        threading.Thread(target=jobs.loop, args=(kind,), daemon=True).start()
    for lane in ("local", "description"):
        threading.Thread(target=pipeline.loop, args=(lane,), daemon=True).start()
    http.serve_forever()
