#!/usr/bin/env python3
"""Standalone local authorization; no library jobs are started."""
import argparse, html, json, sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit, parse_qs
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import subscription

p = argparse.ArgumentParser()
p.add_argument("--port", type=int, default=8766)
p.add_argument("--account")
a = p.parse_args()

class Callback(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        if self.headers.get("Host") != f"127.0.0.1:{a.port}" or urlsplit(self.path).path != "/oauth/chatgpt/callback":
            self.send_error(404)
            return
        try:
            subscription.finish(parse_qs(urlsplit(self.path).query))
            message = "ChatGPT connected. You can return to Bookmark Library. No import has started."
        except Exception:
            message = "Connection did not complete. Return to Bookmark Library and start a new sign-in."
        body = ("<!doctype html><title>Bookmark Library</title><h1>Bookmark Library</h1><p>" + html.escape(message) + "</p>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)
        print(message, flush=True)

server = HTTPServer(("127.0.0.1", a.port), Callback)
a.port = server.server_port
print(json.dumps(subscription.begin(a.port, a.account)), flush=True)
server.serve_forever()
