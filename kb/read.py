"""Read a full document from the KB by doc_id or URL (agentic retrieval step 2).

    python3 -m kb.read 42
    python3 -m kb.read --url "https://docs.workato.com/en/formulas/intro.md"
"""

from __future__ import annotations

import argparse
import sqlite3

from .indexer import get_db


def read(doc_id: int | None = None, url: str | None = None, max_chars: int = 24000) -> str:
    db = get_db()
    try:
        if url:
            row = db.execute(
                "SELECT doc_id, source, url, title, section, path FROM docs WHERE url=?", (url,)
            ).fetchone()
        else:
            row = db.execute(
                "SELECT doc_id, source, url, title, section, path FROM docs WHERE doc_id=?", (doc_id,)
            ).fetchone()
        if not row:
            return f"doc not found (doc_id={doc_id}, url={url})"
        did, source, u, title, section, path = row
        chunks = db.execute(
            "SELECT chunk_ord, context, text FROM chunks WHERE doc_id=? ORDER BY chunk_ord", (did,)
        ).fetchall()
        parts = [f"# {title}", f"source: {source} | section: {section} | url: {u} | path: {path}", ""]
        total = 0
        for _, _, text in chunks:
            parts.append(text)
            total += len(text)
            if total > max_chars:
                parts.append(f"\n[... truncated at {max_chars} chars of {total+sum(len(t) for _,_,t in chunks)}]")
                break
        return "\n".join(parts)
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_id", nargs="?", type=int)
    ap.add_argument("--url")
    a = ap.parse_args()
    print(read(doc_id=a.doc_id, url=a.url))
