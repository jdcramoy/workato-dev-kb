"""kb MCP server — exposes the RAG++ KB as native tools over stdio.

Any MCP client (Claude Code, Codex, claude.ai connectors) gets kb_search,
kb_read, kb_zoom without shell access. Add to a client config:

    { "mcpServers": { "workato-kb": { "command": "<repo>/.venv/bin/python",
      "args": ["-m", "kb.mcp_server"] } } }

Tools: kb_search (hybrid RAG++), kb_read (full doc), kb_zoom (fractal zoom).

Daemon-first, same contract as the kb CLI: tool calls proxy to the warm
kb-daemon (127.0.0.1:8765) so N concurrent MCP clients share ONE index copy
instead of each loading ~3.4GB in-process. Cold in-process fallback (full
index load, ~10s) only when the daemon is unreachable.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DAEMON = os.environ.get("KB_DAEMON_URL", "http://127.0.0.1:8765")


def _tool_specs() -> list[dict]:
    return [
        {
            "name": "kb_search",
            "description": "Search the Workato RAG++ knowledge base (docs, changelog, blog, "
            "EIP, GitHub architecture canon, Claude docs). Hybrid BM25 + "
            "dense + rerank with entropy/PMI modulation. Returns cited hits.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer", "default": 8},
                    "source": {"type": "string", "description": "docs|changelog|blog|inspiration|github|eip|claude"},
                    "section": {"type": "string"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "kb_read",
            "description": "Read a full document by doc_id or URL (agentic step 2 after kb_search).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "integer"},
                    "url": {"type": "string"},
                },
            },
        },
        {
            "name": "kb_zoom",
            "description": "Fractal zoom: coarse-first retrieval across the factlet levels "
            "(topic/file/section/chunk) with drill-down pointers.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "level": {"type": "integer", "description": "0 topic, 1 file, 2 section, 3 chunk"},
                    "k": {"type": "integer", "default": 6},
                },
                "required": ["topic"],
            },
        },
    ]


def _daemon_call(path: str, payload: dict) -> dict | None:
    """POST to the kb-daemon. Returns the parsed response, or None when the
    daemon is unreachable (caller falls back to cold in-process). Daemon 5xx
    answers are raised — the daemon answers errors, never dies silently."""
    req = urllib.request.Request(
        f"{DAEMON}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"kb-daemon {e.code}: {body}") from e
    except urllib.error.URLError:
        return None  # daemon down -> cold fallback


# tool name -> (daemon endpoint, payload builder)
_DAEMON_ROUTES = {
    "kb_search": ("/search", lambda a: {"q": a["query"], "k": a.get("k", 8), "source": a.get("source"), "section": a.get("section")}),
    "kb_read": ("/read", lambda a: {"doc_id": a.get("doc_id"), "url": a.get("url")}),
    "kb_zoom": ("/zoom", lambda a: {"topic": a["topic"], "level": a.get("level"), "k": a.get("k", 6)}),
}


def _call(name: str, args: dict) -> dict:
    route = _DAEMON_ROUTES.get(name)
    if route is not None:
        path, payload = route
        try:
            resp = _daemon_call(path, payload(args))
        except (KeyError, TypeError):
            resp = None  # malformed args -> let in-process path produce the error
        if resp is not None:
            if "error" in resp:
                raise RuntimeError(resp["error"])
            return {"content": [{"type": "text", "text": resp.get("text", "")}]}

    # cold fallback: full in-process index load (~3.4GB, ~10s) — daemon must be down
    if name == "kb_search":
        from .search import search, format_hits

        hits, meta = search(args["query"], k=args.get("k", 8), source=args.get("source"))
        return {"content": [{"type": "text", "text": format_hits(hits, meta)}]}
    if name == "kb_read":
        from .read import read

        return {"content": [{"type": "text", "text": read(doc_id=args.get("doc_id"), url=args.get("url"))}]}
    if name == "kb_zoom":
        from .fractal import zoom

        result = zoom(args.get("topic"), args.get("level"), args.get("k", 6))
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
    return {"error": f"unknown tool {name}"}


def main() -> None:
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        rid = req.get("id")
        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "workato-kb", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            }
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": rid, "result": {"tools": _tool_specs()}}
        elif method == "tools/call":
            params = req.get("params", {})
            try:
                result = _call(params.get("name", ""), params.get("arguments", {}))
                resp = {"jsonrpc": "2.0", "id": rid, "result": result}
            except Exception as e:  # noqa: BLE001
                resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(e)[:300]}}
        elif method == "notifications/initialized":
            continue
        else:
            resp = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
