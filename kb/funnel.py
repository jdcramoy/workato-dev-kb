"""funnel.py — Matryoshka-style coarse-fine retrieval (structure, not retraining).

MRL's trick without retraining: the LLM situating lines (one per document,
from kb.contextualize) become the COARSE embedding layer; chunk embeddings
stay the FINE layer. A query embeds once at each resolution:

  1. coarse: query embedding vs doc-context embeddings -> shortlist pages
  2. fine: chunk-level search restricted to the shortlist
  3. fusion: coarse page-affinity boosts chunks from strong pages

Funnel retrieval is hierarchical memory the CLS way: semantic summary layer
above episodic detail.

    python3 -m kb.funnel build     # embed all doc contexts (coarse layer)
    python3 -m kb.funnel search "vua connection mode"   # funnel retrieval
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sqlite3

from .indexer import DB_PATH, get_db, MODEL_NAME

COARSE_TABLE = "docs_vec_coarse"


def build(batch: int = 64) -> dict:
    db = get_db()
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(docs)")}
        if "llm_context" not in cols:
            return {"error": "no llm_context yet — run kb.contextualize first"}
        rows = db.execute(
            "SELECT doc_id, llm_context FROM docs WHERE llm_context != ''"
        ).fetchall()
        if not rows:
            return {"error": "llm_context column empty"}
        db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {COARSE_TABLE} USING vec0("
            "doc_id INTEGER PRIMARY KEY, embedding float[768])"
        )
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(MODEL_NAME)
        done = 0
        for i in range(0, len(rows), batch):
            chunk = rows[i : i + batch]
            texts = [r[1] for r in chunk]
            embs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            for (doc_id, _), emb in zip(chunk, embs):
                # vec0 has no working INSERT OR REPLACE: delete-then-insert
                db.execute(f"DELETE FROM {COARSE_TABLE} WHERE doc_id=?", (doc_id,))
                db.execute(
                    f"INSERT INTO {COARSE_TABLE}(doc_id, embedding) VALUES(?,?)",
                    (doc_id, struct.pack(f"{len(emb)}f", *emb)),
                )
            done += len(chunk)
        db.commit()
        return {"coarse_embedded": done, "table": COARSE_TABLE}
    finally:
        db.close()


def search(query: str, k: int = 8, shortlist: int = 60) -> list[dict]:
    """Coarse shortlist (pages) -> fine search (chunks) -> page-affinity boost."""
    db = get_db()
    try:
        try:
            n_coarse = db.execute(f"SELECT COUNT(*) FROM {COARSE_TABLE}").fetchone()[0]
        except sqlite3.OperationalError:
            return [{"error": "coarse layer missing — run kb.funnel build"}]
        from .indexer import get_model

        model = get_model("embed")
        q = model.encode([query], normalize_embeddings=True)[0].tolist()
        qbind = struct.pack(f"{len(q)}f", *q)

        # coarse: nearest PAGES (shortlist)
        pages = db.execute(
            f"SELECT doc_id, distance FROM {COARSE_TABLE} WHERE embedding MATCH ? AND k = {shortlist}",
            (qbind,),
        ).fetchall()
        # design note (tested): the coarse layer's absolute distances cluster
        # tightly (0.72-0.84 for everything relevant), so a distance-based
        # "confident shortlist" filter is a poor discriminator AND a recall
        # risk (it can exclude the true page). The rank-based page-affinity
        # boost carries all the value; the candidate set stays unrestricted.
        page_rank: dict[int, int] = {}
        for rank, (doc_id, _dist) in enumerate(pages):
            page_rank[doc_id] = rank

        # fine: chunks restricted to shortlisted pages, vec0 kNN with page boost
        # MATCH + joins live in ONE inner select (the only vec0 shape the
        # planner accepts); the outer query filters columns only
        inner = f"""SELECT c.chunk_id, c.doc_id, d.url, d.title, d.source, d.section, c.context, c.text,
                           v.distance
                    FROM chunks_vec v
                    JOIN chunks c ON c.chunk_id = v.chunk_id
                    JOIN docs d ON d.doc_id = c.doc_id
                    WHERE v.embedding MATCH ? AND k = {k * 6}"""
        rows = db.execute(
            f"SELECT * FROM ({inner}) ORDER BY distance LIMIT ?",
            [qbind, k * 3],
        ).fetchall()

        # fusion: vec distance (smaller=better) + coarse page-affinity bonus
        out = []
        for row in rows:
            chunk_id, doc_id, url, title, source, section, context, text, dist = row
            coarse_boost = 1.0 - (page_rank.get(doc_id, shortlist) / max(shortlist, 1))
            score = (1.0 - dist) * 0.7 + coarse_boost * 0.3
            out.append(
                {
                    "url": url,
                    "title": title,
                    "source": source,
                    "score": round(score, 4),
                    "vec_distance": round(dist, 4),
                    "page_affinity": round(coarse_boost, 3),
                    "snippet": text[:250],
                }
            )
        for r in out:
            r["funnel"] = "fine+affinity"
        out.sort(key=lambda r: -r["score"])
        return out[:k]
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=8)
    a = ap.parse_args()
    if a.cmd == "build":
        print(json.dumps(build(), indent=2))
    elif a.cmd == "search":
        print(json.dumps(search(a.query, a.k), indent=2))
