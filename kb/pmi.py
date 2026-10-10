"""PMI scoring: pointwise mutual information between
query terms and candidate chunks.

PMI(t) = log2( P(t | chunk) / P(t | corpus) ) summed over query terms present in
the chunk, normalized by chunk length. High PMI = the chunk concentrates terms
that are rare corpus-wide — distinctive, on-target specificity — while generic
prose that merely *mentions* a query word scores near zero.

Term stats are persisted once per index build (terms.json.gz) and reused.

    python3 -m kb.pmi build     # build/persist corpus term probabilities
    python3 -m kb.pmi probe "safe navigation operator null"
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
from pathlib import Path

from .common import DATA_ROOT
from .indexer import get_db

TERMS_PATH = DATA_ROOT / "index" / "terms.json.gz"
_WORD_RE = re.compile(r"[a-z][a-z0-9_\-]{2,}")

try:
    # comprehensive standard list (318 words) — covers prose filler comprehensively
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS as _SK_STOP

    _STOP = set(_SK_STOP)
except ImportError:  # noqa: Fallback if sklearn missing
    _STOP = set(
        """the and for are but not you all any can was one our out get has how this that with when your will which then them these some more just like also into over such only than where why""".split()
    )
_STOP.update(
    """workato workato's page pages doc docs documentation documentation's section sections
    thing things stuff lot lots way ways want wants wanted need needs needed know knows known
    use uses used using make makes made take takes taken give given going get gets got getting
    i'm it's don't we're they're you're can't won't didn't doesn't isn't wasn't that's there's
    http https www com md wiki able happens happen happening happened having having""".split()
)


def is_stop(word: str) -> bool:
    """Stopword check with inflection stripping: 'having'->'have', 'taking'->'take'."""
    if word in _STOP:
        return True
    stem = word
    for suffix in ("ing", "ed", "er", "es", "s", "ly"):
        if stem.endswith(suffix) and len(stem) - len(suffix) >= 3:
            stem = stem[: -len(suffix)]
            break
    if stem != word:
        if stem in _STOP:
            return True
        # 'having'->'hav' misses 'have'; try restoring trailing e
        if (stem + "e") in _STOP:
            return True
        if stem[-1] == stem[-2] and stem[:-1] in _STOP:  # 'running'->'runn'->'run'
            return True
    return False


def build() -> dict:
    """Compute corpus term probabilities and persist (gzip json: term -> prob)."""
    db = get_db()
    try:
        counts: dict[str, int] = {}
        total = 0
        (n,) = db.execute("SELECT COUNT(*) FROM chunks").fetchone()
        step = 4000
        for off in range(0, n, step):
            rows = db.execute(
                "SELECT text FROM chunks ORDER BY chunk_id LIMIT ? OFFSET ?", (step, off)
            ).fetchall()
            for (text,) in rows:
                for w in _WORD_RE.findall(text.lower()):
                    if is_stop(w):
                        continue
                    counts[w] = counts.get(w, 0) + 1
                    total += 1
        probs = {w: c / total for w, c in counts.items() if c >= 2}  # drop hapax noise
        TERMS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(TERMS_PATH, "wt") as f:
            json.dump({"total_words": total, "probs": probs}, f, separators=(",", ":"))
        return {"terms": len(probs), "total_words": total, "path": str(TERMS_PATH)}
    finally:
        db.close()


def _load_probs() -> dict[str, float]:
    if not TERMS_PATH.exists():
        build()
    with gzip.open(TERMS_PATH, "rt") as f:
        return json.load(f)["probs"]


def query_terms(query: str) -> list[str]:
    return [w for w in _WORD_RE.findall(query.lower()) if w not in _STOP]


def pmi_score(query: str, chunk_text: str, probs: dict[str, float] | None = None) -> float:
    """Normalized PMI between query and chunk text. 0 when no overlap."""
    probs = probs or _load_probs()
    terms = query_terms(query)
    if not terms:
        return 0.0
    words = _WORD_RE.findall(chunk_text.lower())
    n = len(words) or 1
    tf: dict[str, int] = {}
    for w in words:
        tf[w] = tf.get(w, 0) + 1
    score = 0.0
    for t in terms:
        if t in tf and t in probs:
            p_in_chunk = tf[t] / n
            score += math.log2(p_in_chunk / probs[t])
    return score / len(terms)  # normalize: bits per query term


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    pr = sub.add_parser("probe")
    pr.add_argument("query")
    pr.add_argument("--text")
    a = ap.parse_args()
    if a.cmd == "build":
        print(json.dumps(build(), indent=2))
    elif a.cmd == "probe":
        if a.text:
            print(f"{pmi_score(a.query, a.text):.3f}")
        else:
            from .indexer import get_db as gdb

            db = gdb()
            rows = db.execute(
                "SELECT text FROM chunks WHERE chunks_fts MATCH ? LIMIT 3",
                (" OR ".join(query_terms(a.query)) or a.query,),
            ).fetchall()
            db.close()
            for (t,) in rows:
                print(f"{pmi_score(a.query, t):.3f} | {t[:90]}")
