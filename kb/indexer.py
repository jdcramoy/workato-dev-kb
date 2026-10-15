"""Build the unified index: contextual chunks -> SQLite FTS5 (BM25) + sqlite-vec embeddings.

Chunking strategy (phase-1 RAG++):
- Split markdown on heading boundaries; target <= ~1200 tokens per chunk.
- Contextual chunking (Anthropic-style, deterministic variant): every chunk is
  prepended with a context header naming source, section path, page title, and
  canonical URL, so each chunk is self-describing when retrieved in isolation.
- Incremental: chunks keyed by (doc_sha256, chunk_ord); re-chunking a doc deletes
  its old rows only when the doc sha changed.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import struct
from pathlib import Path

from .common import DATA_ROOT, load_manifest

DB_PATH = DATA_ROOT / "index" / "kb.db"
_MODEL_CACHE: dict = {}


def get_model(kind: str = "embed"):
    """Process-wide model cache: load once, reuse for every query.
    (Resident servers amortize the ~6s load; CLI calls still pay it.)"""
    if kind not in _MODEL_CACHE:
        from sentence_transformers import SentenceTransformer, CrossEncoder

        if kind == "embed":
            _MODEL_CACHE[kind] = SentenceTransformer(MODEL_NAME)
        elif kind == "rerank":
            _MODEL_CACHE[kind] = CrossEncoder("BAAI/bge-reranker-base")
    return _MODEL_CACHE[kind]
MODEL_NAME = "BAAI/bge-base-en-v1.5"
CHUNK_TARGET_CHARS = 4800  # ~1200 tokens
MAX_DOCS_PER_RUN = 0  # 0 = all

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    doc_id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    title TEXT NOT NULL,
    section TEXT DEFAULT '',
    sha256 TEXT NOT NULL,
    indexed_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES docs(doc_id) ON DELETE CASCADE,
    chunk_ord INTEGER NOT NULL,
    context TEXT NOT NULL,
    text TEXT NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    context, text, content='chunks', content_rowid='chunk_id'
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
    chunk_id INTEGER PRIMARY KEY,
    embedding float[768]
);
"""


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.enable_load_extension(True)
    import sqlite_vec

    sqlite_vec.load(db)  # provides the vec0 virtual-table module
    db.enable_load_extension(False)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    if "llm_context" not in {r[1] for r in db.execute("PRAGMA table_info(docs)")}:
        db.execute("ALTER TABLE docs ADD COLUMN llm_context TEXT DEFAULT ''")
    return db


def _strip_frontmatter(text: str) -> tuple[dict, str]:
    meta = {}
    body = text
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                m = re.match(r'(\w+):\s*"?(.*?)"?$', line.strip())
                if m:
                    meta[m.group(1)] = m.group(2)
            body = parts[2]
    return meta, body


def _split_headings(body: str) -> list[str]:
    """Split on ## and ### boundaries, merging tiny sections into neighbors."""
    parts = re.split(r"\n(?=#{2,3} )", body)
    out: list[str] = []
    for p in parts:
        if out and len(p) < 200:
            out[-1] += "\n" + p
        elif len(p) <= CHUNK_TARGET_CHARS:
            out.append(p)
        else:
            # long section: sliding split on paragraphs
            cur = ""
            for para in p.split("\n\n"):
                if cur and len(cur) + len(para) > CHUNK_TARGET_CHARS:
                    out.append(cur)
                    cur = para
                else:
                    cur = f"{cur}\n\n{para}" if cur else para
            if cur:
                out.append(cur)
    # second pass: merge tiny leftovers forward
    merged: list[str] = []
    for p in out:
        if merged and len(p) < 150:
            merged[-1] += "\n" + p
        else:
            merged.append(p)
    return [p for p in merged if p.strip()]


def _doc_iter():
    """Yield (source, url, path, title, section, sha256, text) for every manifest record.

    Discovery is fully manifest-driven: every manifest/*.jsonl is a source
    (confluence-<alias> labels as confluence/<alias>). A newly-wired source
    syncs, manifests, and indexes with no edit here — the old hardcoded dict
    silently excluded the 2026-10-11 scout sources (same bug class as the
    confluence-space one fixed 2026-10-10).
    """
    sources = sorted(mp.stem for mp in (DATA_ROOT / "manifest").glob("*.jsonl"))
    corpus = DATA_ROOT / "corpus"
    for source in sources:
        manifest = load_manifest(source)
        for url, rec in manifest.items():
            rel = rec.get("path", "")
            if not rel:
                continue
            f = corpus / rel
            if not f.exists():
                continue
            yield (
                source,
                url,
                rel,
                rec.get("title", ""),
                rec.get("section", rec.get("space", "")),
                rec.get("sha256", ""),
                f.read_text(),
            )


def _embed_texts(texts: list[str]) -> list[list[float]]:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)
    vecs = []
    batch = 128
    for i in range(0, len(texts), batch):
        emb = model.encode(texts[i : i + batch], normalize_embeddings=True, show_progress_bar=False)
        vecs.extend(emb.tolist())
        if i % 1280 == 0:
            print(f"  embedded {i + len(texts[i:i+batch])}/{len(texts)}", flush=True)
    return vecs


def build(force: bool = False, with_embeddings: bool = True) -> dict:
    db = get_db()
    stats = {"docs_added": 0, "docs_updated": 0, "chunks": 0, "embedded": 0, "removed": 0}

    all_docs = list(_doc_iter())
    emb_texts: list[tuple[int, str]] = []  # (chunk_id, context+text)

    for source, url, rel, title, section, sha, raw in all_docs:
        row = db.execute("SELECT doc_id, sha256, llm_context FROM docs WHERE url=?", (url,)).fetchone()
        if row and row[1] == sha and not force:
            continue
        llm_ctx = (row[2] or "").strip() if row else ""
        meta, body = _strip_frontmatter(raw)
        # section fallback: frontmatter source field (blog/changelog) or manifest section
        sec = section or meta.get("source", "") or meta.get("space", "")
        if row:
            db.execute("DELETE FROM chunks WHERE doc_id=?", (row[0],))
            db.execute("DELETE FROM docs WHERE doc_id=?", (row[0],))
            stats["docs_updated"] += 1
        else:
            stats["docs_added"] += 1
        cur = db.execute(
            "INSERT INTO docs(source,url,path,title,section,sha256,llm_context) VALUES(?,?,?,?,?,?,?)",
            (source, url, rel, title, sec, sha, llm_ctx),
        )
        doc_id = cur.lastrowid
        context_header = f"[source: {source} | section: {sec} | page: {title} | url: {url}]"
        if llm_ctx:
            context_header = f"[{source} | {sec} | {title}] {llm_ctx} (url: {url})"
        for ordn, chunk in enumerate(_split_headings(body)):
            chunk_sha = hashlib.sha256(chunk.encode()).hexdigest()
            c = db.execute(
                "INSERT INTO chunks(doc_id,chunk_ord,context,text,sha256) VALUES(?,?,?,?,?)",
                (doc_id, ordn, context_header, chunk, chunk_sha),
            )
            emb_texts.append((c.lastrowid, f"{context_header}\n\n{chunk}"))
            stats["chunks"] += 1
        db.commit()

    # purge docs no longer in manifests
    live_urls = {u for _, u, *_ in all_docs}
    for (url,) in db.execute("SELECT url FROM docs").fetchall():
        if url not in live_urls:
            db.execute("DELETE FROM docs WHERE url=?", (url,))
            stats["removed"] += 1
    # purge stale vector rows (vec0 has no FK cascade)
    db.execute(
        "DELETE FROM chunks_vec WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)"
    )
    db.commit()

    if with_embeddings and emb_texts:
        print(f"embedding {len(emb_texts)} chunks with {MODEL_NAME} ...")
        vecs = _embed_texts([t for _, t in emb_texts])
        for (cid, _), v in zip(emb_texts, vecs):
            db.execute(
                "INSERT OR REPLACE INTO chunks_vec(chunk_id,embedding) VALUES(?,?)",
                (cid, struct.pack(f"{len(v)}f", *v)),
            )
        stats["embedded"] = len(vecs)
        db.commit()

    # self-heal: only chunks INSERTED in a run get embedded there. A build
    # killed mid-embed leaves committed chunks vectorless, and later runs
    # skip unchanged docs — nothing would ever backfill them (2026-10-14:
    # 4,535 slack chunks stranded this way by a killed index step). Embed
    # text is reconstructable from the stored context header + chunk body.
    if with_embeddings:
        missing = db.execute(
            "SELECT c.chunk_id, c.context, c.text FROM chunks c "
            "LEFT JOIN chunks_vec v ON v.chunk_id = c.chunk_id "
            "WHERE v.chunk_id IS NULL"
        ).fetchall()
        if missing:
            print(f"backfilling {len(missing)} unembedded chunks ...", flush=True)
            vecs = _embed_texts([f"{ctx}\n\n{text}" for _, ctx, text in missing])
            for (cid, _, _), v in zip(missing, vecs):
                db.execute(
                    "INSERT OR REPLACE INTO chunks_vec(chunk_id,embedding) VALUES(?,?)",
                    (cid, struct.pack(f"{len(v)}f", *v)),
                )
            stats["backfilled"] = len(vecs)
            db.commit()

    db.close()
    return stats


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-embed", action="store_true")
    a = ap.parse_args()
    print(json.dumps(build(force=a.force, with_embeddings=not a.no_embed), indent=2))
