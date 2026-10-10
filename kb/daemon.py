"""kb-daemon — the always-warm embedding server.

One resident process holds the embedding + rerank models loaded; the kb CLI
talks to it over localhost HTTP and answers in milliseconds. launchd keeps
it alive (KeepAlive); the CLI falls back to cold in-process loading if the
daemon is down — the CLI "works properly" either way.

    python3 -m kb.daemon [--port 8765]

Endpoints: /health, /search (q, k, source, section), /read (doc_id|url),
/zoom (topic, level, k).
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()  # torch encode: serialize to be safe
READY = {"embed": False, "rerank": False}


def _models():
    from kb.indexer import get_model

    get_model("embed")
    get_model("rerank")
    READY["embed"] = READY["rerank"] = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send({"ok": True, "warm": READY})
            return
        self._send({"error": "use POST"}, 405)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            args = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, ValueError):
            self._send({"error": "bad json"}, 400)
            return
        try:
            with LOCK:
                if self.path == "/search":
                    from kb.search import search, format_hits

                    hits, meta = search(
                        args["q"], k=args.get("k", 8), source=args.get("source"),
                        section=args.get("section"),
                    )
                    self._send({"text": format_hits(hits, meta), "n": len(hits)})
                elif self.path == "/read":
                    from kb.read import read

                    self._send({"text": read(doc_id=args.get("doc_id"), url=args.get("url"))})
                elif self.path == "/zoom":
                    from kb.fractal import zoom

                    self._send({"text": json.dumps(zoom(args["topic"], args.get("level"), args.get("k", 6)), indent=2)})
                else:
                    self._send({"error": f"unknown endpoint {self.path}"}, 404)
        except Exception as e:  # noqa: BLE001 - daemon answers errors, never dies
            self._send({"error": str(e)[:300]}, 500)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()

    # warm BEFORE binding: the port opens only when the model is ready
    _models()
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"kb-daemon warm on 127.0.0.1:{a.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
