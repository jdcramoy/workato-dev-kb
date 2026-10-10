"""Data-entropy scoring (RAG++ layer 3).

Concept: rank chunks by how much *unique information* they carry relative to the
corpus, so retrieval can prefer rare, high-value specifics over boilerplate that
repeats across hundreds of pages.

Method (unigram Shannon entropy / self-information):
  1. Build a corpus unigram distribution P(word) from all chunk texts.
  2. A chunk's self-information = sum over words of -log2 P(word)  (bits).
  3. Normalize by chunk length -> bits/word "surprise density".
  4. High surprise density = rare terminology = distinctive content (a formula
     gotcha, a connector quirk). Low = boilerplate (nav text, marketing intro).
  5. Optional re-rank weight: final = rerank_score * (1 + w * surprise_density_norm).

CLI:
  python3 -m kb.entropy score            # compute + store entropy per chunk
  python3 -m kb.entropy top -n 25        # highest-surprise chunks in the corpus
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import Counter

from .indexer import get_db

_WORD_RE = re.compile(r"[a-z][a-z0-9_\-]{2,}")
_STOP = set(
    """the and for are but not you all any can her was one our out day get has him his how man new now old see two way who boy did its let put say she too use with this that from have they what when your will each which their said many then them these some more very just like also into over such only than
""".split()
)


def corpus_unigrams(db: sqlite3.Connection) -> Counter:
    counts: Counter = Counter()
    (total,) = db.execute("SELECT COUNT(*) FROM chunks").fetchone()
    step = 2000
    for off in range(0, total, step):
        rows = db.execute(
            "SELECT text FROM chunks ORDER BY chunk_id LIMIT ? OFFSET ?", (step, off)
        ).fetchall()
        for (text,) in rows:
            counts.update(w for w in _WORD_RE.findall(text.lower()) if w not in _STOP)
    return counts


def surprise_density(words: list[str], probs: dict[str, float]) -> float:
    if not words:
        return 0.0
    bits = sum(-math.log2(probs.get(w, 1e-9)) for w in words)
    return bits / len(words)


def score() -> dict:
    db = get_db()
    try:
        db.execute(
            "ALTER TABLE chunks ADD COLUMN entropy REAL DEFAULT 0"
        ) if "entropy" not in {r[1] for r in db.execute("PRAGMA table_info(chunks)")} else None
        unigrams = corpus_unigrams(db)
        total = sum(unigrams.values())
        probs = {w: c / total for w, c in unigrams.items()}
        rows = db.execute("SELECT chunk_id, text FROM chunks").fetchall()
        scores = []
        for cid, text in rows:
            words = [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP]
            s = surprise_density(words, probs)
            scores.append((cid, s))
            db.execute("UPDATE chunks SET entropy=? WHERE chunk_id=?", (s, cid))
        db.commit()
        vals = [s for _, s in scores]
        return {
            "chunks_scored": len(scores),
            "mean_bits_per_word": round(sum(vals) / len(vals), 2) if vals else 0,
            "p95": round(sorted(vals)[int(len(vals) * 0.95)], 2) if vals else 0,
        }
    finally:
        db.close()


def top(n: int = 25) -> list[dict]:
    db = get_db()
    try:
        rows = db.execute(
            """SELECT c.chunk_id, d.url, d.title, c.entropy, substr(c.text, 1, 160)
               FROM chunks c JOIN docs d ON d.doc_id = c.doc_id
               WHERE c.entropy > 0 ORDER BY c.entropy DESC LIMIT ?""",
            (n,),
        ).fetchall()
        return [
            {"chunk_id": r[0], "url": r[1], "title": r[2], "entropy": round(r[3], 2), "snippet": r[4]}
            for r in rows
        ]
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("score")
    t = sub.add_parser("top")
    t.add_argument("-n", type=int, default=25)
    a = ap.parse_args()
    if a.cmd == "score":
        print(json.dumps(score(), indent=2))
    elif a.cmd == "top":
        print(json.dumps(top(a.n), indent=2))
