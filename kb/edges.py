"""Entity-based document edges with per-source IDF (side-car graph layer).

Builds cross-source "related document" edges from shared rare entities:
connector names, error codes, camelCase/snake_case identifiers, capitalized
product names, hyphenated technical terms, and rare single tokens.

Canonical storage convention: each edge is stored ONCE with
(src_url, dst_url) in sorted (lexicographic) order. Direction is implied by
the sort -- the edge is undirected. Consumers should treat the pair as a set:
to find all edges touching url U, query
    WHERE src_url = U OR dst_url U
and take the other endpoint as the neighbor. Never assume src -> dst means
"flows into"; it only means "the lexicographically smaller url sorts first".

Entity rarity is computed PER SOURCE: a term is an entity only when
  - df(s, term) / n_docs(s) <= 0.02 for EVERY source s where it appears, and
  - total df across the whole corpus <= 50 docs.
Edge rule: two docs of DIFFERENT sources sharing >= 2 entities get one edge.
Score = sum over shared entities of min-per-source IDF (natural log of
n_docs(s) / df(s, term)). Rationale = up to 5 shared entities, highest IDF
first, comma-joined. Same-source pairs are skipped on purpose: docs within
one source usually already cross-link, and entity overlap there is noise.

Idempotent rebuild: DROP-equivalent (DELETE ... WHERE edge_type='entity')
then reinsert. The table may later hold other edge types alongside.

  python3 -m kb.edges build
  python3 -m kb.edges stats
  python3 -m kb.edges sample [N]   # N random edges for eyeball QA (default 10)
"""

from __future__ import annotations

import math
import random
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

from .common import DATA_ROOT

DB_PATH = DATA_ROOT / "index" / "kb.db"

EDGE_TYPE = "entity"
MIN_SHARED_ENTITIES = 2
MAX_EDGES_PER_DOC = 8
MAX_RATIONALE = 5
MAX_DF = 50            # total df cap across corpus
RARE_IN_SOURCE = 0.02  # df/n(source) cap in EVERY source
MAX_SHARED_PER_PAIR = 64  # memory bound: stop collecting shared entities past this

# Sentence-leading words that make "Capitalized Multiword" matches junk
# (e.g. "The Recipe", "Click Settings"). Deterministic stoplist, no LLM.
_CAP_STOP = {
    "a", "all", "also", "an", "and", "as", "at", "but", "by", "can", "click",
    "for", "from", "get", "how", "if", "in", "into", "is", "it", "its", "may",
    "more", "must", "new", "no", "not", "note", "now", "off", "on", "or",
    "our", "per", "select", "set", "step", "that", "the", "their", "then",
    "there", "these", "this", "to", "use", "using", "via", "we", "what",
    "when", "why", "with", "you", "your",
}

# One combined scan per doc text. Alternation order = priority (first hit wins).
CANDIDATE_RE = re.compile(
    r"(?P<error>[A-Z]{2,4}[-_]\d{3,6})"                               # ERR-404, SC_0012
    r"|(?P<snake>\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b)"                  # snake_case_id
    r"|(?P<camel>\b[a-z]+(?:[A-Z][a-zA-Z0-9]*)+\b)"                   # camelCaseId
    r"|(?P<capmulti>\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){1,3}\b)"  # Capitalized Multiword
    r"|(?P<hyphen>\b[a-z]+(?:-[a-z0-9]+)+\b)"                         # hyphenated-term
    r"|(?P<single>\b[a-zA-Z][a-zA-Z0-9_]{7,}\b)"                      # rare single tokens
)


def _extract_terms(text: str) -> dict[str, str]:
    """term -> regex kind for one doc text."""
    out: dict[str, str] = {}
    for m in CANDIDATE_RE.finditer(text):
        kind = m.lastgroup
        term = m.group()
        if kind == "capmulti":
            words = term.split()
            if any(w.lower() in _CAP_STOP for w in words):
                continue
            # require a real multiword name: last word not a stopword either
            if words[-1].lower() in _CAP_STOP:
                continue
            out.setdefault(" ".join(words), kind)
        elif kind == "single":
            # skip pure numbers and trivial repeated-letter junk
            if term.isdigit():
                continue
            out.setdefault(term, kind)
        else:
            out.setdefault(term, kind)
    return out


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS doc_edges(
               src_url TEXT NOT NULL,
               dst_url TEXT NOT NULL,
               edge_type TEXT NOT NULL,
               score REAL NOT NULL,
               rationale TEXT NOT NULL,
               generated_at TEXT NOT NULL,
               UNIQUE(src_url, dst_url, edge_type)
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_doc_edges_src ON doc_edges(src_url, edge_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_doc_edges_dst ON doc_edges(dst_url, edge_type)"
    )


def _fold_terms(
    raw: dict[str, str],
) -> tuple[set[str], dict[str, str], dict[str, str]]:
    """Deterministic entity-identity folding on one doc's term->kind map.

    - lowercase key: 'Conference' and 'conference' are the same entity
    - plural merge: if both 'conference' and 'conferences' are candidates in
      the doc, collapse onto the singular so one word can't count twice as
      two shared entities
    Returns (folded term set, folded_key -> display form, folded_key -> kind).
    """
    lower_disp: dict[str, str] = {}
    lower_kind: dict[str, str] = {}
    for t, kind in raw.items():
        k = t.lower()
        # prefer the form with more internal structure for display
        if k not in lower_disp or (t[0].isupper() and not lower_disp[k][0].isupper()):
            lower_disp[k] = t
        if k not in lower_kind or kind != "single":
            lower_kind[k] = kind
    folded = set(lower_disp)
    for k in list(folded):
        if k.endswith("s") and len(k) > 3 and k[:-1] in folded:
            folded.discard(k)
            lower_disp[k[:-1]] = lower_disp.get(k[:-1], lower_disp[k])
            lower_kind[k[:-1]] = lower_kind.get(k[:-1], lower_kind[k])
    return folded, lower_disp, lower_kind


def load_doc_terms(
    conn: sqlite3.Connection,
) -> tuple[dict[int, set[str]], dict[str, str], dict[str, str]]:
    """Doc-level term extraction: stream chunks grouped by doc_id (memory-safe),
    extract candidate terms per doc. One pass over chunks, no chunk-level
    fan-out. Returns (doc -> folded term set, folded key -> display form,
    folded key -> kind)."""
    terms: dict[int, set[str]] = {}
    canon_count: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    kind_count: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    cur = conn.execute("SELECT doc_id, text FROM chunks ORDER BY doc_id")
    current_doc = None
    buf: list[str] = []
    for doc_id, text in cur:
        if doc_id != current_doc:
            if current_doc is not None:
                terms[current_doc] = _extract_terms(" ".join(buf))
            current_doc = doc_id
            buf = []
        buf.append(text or "")
    if current_doc is not None:
        terms[current_doc] = _extract_terms(" ".join(buf))

    folded_terms: dict[int, set[str]] = {}
    for doc_id, tmap in terms.items():
        folded, disp, kind = _fold_terms(tmap)
        folded_terms[doc_id] = folded
        for k in folded:
            canon_count[k][disp[k]] += 1
            kind_count[k][kind[k]] += 1

    canon = {k: max(forms.items(), key=lambda x: (x[1], x[0]))[0]
             for k, forms in canon_count.items()}
    kinds = {k: max(forms.items(), key=lambda x: (x[1], x[0]))[0]
             for k, forms in kind_count.items()}
    return folded_terms, canon, kinds


def build(db_path: sqlite3.Connection | str | None = None) -> dict:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    _ensure_table(conn)

    docs = conn.execute("SELECT doc_id, url, source FROM docs").fetchall()
    url_of = {doc_id: url for doc_id, url, _ in docs}
    source_of = {doc_id: src for doc_id, _, src in docs}
    n_docs_by_source: dict[str, int] = defaultdict(int)
    for _, _, src in docs:
        n_docs_by_source[src] += 1

    print("[edges] extracting candidate terms (doc-level) ...", flush=True)
    doc_terms, canon_form, kind_of = load_doc_terms(conn)

    # per-source df and total df
    df_by_source: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    df_total: dict[str, int] = defaultdict(int)
    for doc_id, tset in doc_terms.items():
        src = source_of[doc_id]
        for t in tset:
            df_by_source[src][t] += 1
            df_total[t] += 1

    # entity filter: rare in EVERY source where it appears + global cap
    print("[edges] filtering entities by per-source rarity ...", flush=True)
    entities: set[str] = set()
    for t, total in df_total.items():
        if total < MIN_SHARED_ENTITIES or total > MAX_DF:
            continue
        if all(
            df_by_source[s][t] / n_docs_by_source[s] <= RARE_IN_SOURCE
            for s, d in df_by_source.items() if t in d
        ):
            entities.add(t)

    # idf per (source, term)
    idf: dict[tuple[str, str], float] = {}
    for t in entities:
        for s in df_by_source:
            d = df_by_source[s].get(t, 0)
            if d:
                idf[(s, t)] = math.log(n_docs_by_source[s] / d)

    # inverted index entity -> [(source, doc_id)]
    inv: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for doc_id, tset in doc_terms.items():
        src = source_of[doc_id]
        for t in tset:
            if t in entities:
                inv[t].append((src, doc_id))

    print("[edges] accumulating cross-source shared entities ...", flush=True)
    shared: dict[tuple[int, int], list[str]] = defaultdict(list)
    for t, postings in inv.items():
        if len(postings) < 2:
            continue
        for i, (s1, d1) in enumerate(postings):
            for s2, d2 in postings[i + 1:]:
                if s1 == s2:
                    continue
                key = (d1, d2) if d1 < d2 else (d2, d1)
                lst = shared[key]
                if len(lst) < MAX_SHARED_PER_PAIR:
                    lst.append(t)

    # An edge needs at least one STRUCTURED shared entity (snake/camel/error-
    # code/hyphen/multiword). Pairs whose only overlap is plain rare single
    # words ("approaching", "categorizes") are noise -- rare but meaningless.
    structured_shared: dict[tuple[int, int], bool] = {}
    for key, ents in shared.items():
        structured_shared[key] = any(kind_of.get(t) != "single" for t in ents)

    # score + rationale + per-doc top-K cap
    print(f"[edges] scoring {len(shared)} candidate doc pairs ...", flush=True)
    best_per_doc: dict[int, list[tuple[float, str, str, str]]] = defaultdict(list)
    for (d1, d2), ents in shared.items():
        if len(ents) < MIN_SHARED_ENTITIES or not structured_shared[(d1, d2)]:
            continue
        s1, s2 = source_of[d1], source_of[d2]
        uniq = set(ents)
        scored = sorted(
            ((idf[(s1, t)], t, canon_form.get(t, t)) for t in uniq),
            key=lambda x: -x[0],
        )
        score = sum(
            min(idf[(s1, t)], idf[(s2, t)]) for _, t, _ in scored
        )
        rationale = ", ".join(disp for _, _, disp in scored[:MAX_RATIONALE])
        for d in (d1, d2):
            best_per_doc[d].append((score, url_of[d1], url_of[d2], rationale))

    now = datetime.now(timezone.utc).isoformat()
    final = _final_edges(best_per_doc, url_of)
    capped = sum(
        1 for cands in best_per_doc.values() if len(cands) > MAX_EDGES_PER_DOC
    )

    conn.execute("DELETE FROM doc_edges WHERE edge_type=?", (EDGE_TYPE,))
    conn.executemany(
        "INSERT OR IGNORE INTO doc_edges VALUES (?,?,?,?,?,?)",
        [(u1, u2, EDGE_TYPE, score, rationale, now) for score, u1, u2, rationale in final],
    )
    conn.commit()

    n_edges = len(final)
    conn.close()
    return {
        "edges": n_edges,
        "docs_capped": capped,
        "docs_with_edges": len(best_per_doc),
        "total_docs": len(docs),
    }


def _final_edges(best_per_doc, url_of):
    """Deduplicate per-doc candidate lists into the final canonical edge set
    (sorted url pair, top MAX_EDGES_PER_DOC by score per doc)."""
    seen: set[tuple[str, str]] = set()
    out = []
    for doc_id, cands in best_per_doc.items():
        for score, u1, u2, rationale in sorted(cands, key=lambda x: -x[0])[:MAX_EDGES_PER_DOC]:
            key = (min(u1, u2), max(u1, u2))
            if key not in seen:
                seen.add(key)
                out.append((score, key[0], key[1], rationale))
    return out


def stats(db_path: sqlite3.Connection | str | None = None) -> None:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    _ensure_table(conn)
    total_docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    n_edges = conn.execute(
        "SELECT COUNT(*) FROM doc_edges WHERE edge_type=?", (EDGE_TYPE,)
    ).fetchone()[0]
    print(f"docs: {total_docs}   entity edges: {n_edges}")

    print("\nper-source-pair bridge matrix (edges):")
    rows = conn.execute(
        """SELECT ds, dd, COUNT(*) FROM (
               SELECT d1.source AS ds, d2.source AS dd
               FROM doc_edges e
               JOIN docs d1 ON d1.url = e.src_url
               JOIN docs d2 ON d2.url = e.dst_url
               WHERE e.edge_type = ?
           ) GROUP BY ds, dd ORDER BY COUNT(*) DESC""",
        (EDGE_TYPE,),
    ).fetchall()
    for ds, dd, c in rows:
        print(f"  {ds:>18} <-> {dd:<18} {c}")

    degs = defaultdict(int)
    for (u,) in conn.execute(
        """SELECT src_url FROM doc_edges WHERE edge_type=?
           UNION ALL SELECT dst_url FROM doc_edges WHERE edge_type=?""",
        (EDGE_TYPE, EDGE_TYPE),
    ):
        degs[u] += 1
    if degs:
        dist = defaultdict(int)
        for d in degs.values():
            dist[min(d // 5 * 5, 36)] += 1
        print("\ndegree distribution (bucket=5):")
        for bucket in sorted(dist):
            print(f"  {bucket:>3}-{bucket + 4:<3} {'#' * min(dist[bucket], 60)} ({dist[bucket]})")
        top = sorted(degs.items(), key=lambda x: -x[1])[:10]
        print("top-degree docs:")
        for u, d in top:
            title = conn.execute("SELECT title FROM docs WHERE url=?", (u,)).fetchone()
            print(f"  {d:>4}  {(title or ['-'])[0][:70]}  {u[:80]}")
    orphans = total_docs - len(degs)
    print(f"\ndocs with zero entity edges (orphans): {orphans} / {total_docs}")
    conn.close()


def sample(n: int = 10, db_path: sqlite3.Connection | str | None = None) -> None:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    _ensure_table(conn)
    rows = conn.execute(
        "SELECT src_url, dst_url, score, rationale FROM doc_edges WHERE edge_type=? ORDER BY RANDOM() LIMIT ?",
        (EDGE_TYPE, n),
    ).fetchall()
    for src, dst, score, rationale in rows:
        print(f"score={score:.2f}")
        print(f"  src: {src}")
        print(f"  dst: {dst}")
        print(f"  via: {rationale}")
        print()
    conn.close()


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "build"
    if cmd == "build":
        import time

        t0 = time.time()
        r = build()
        print(
            f"[edges] done in {time.time() - t0:.1f}s: {r['edges']} edges, "
            f"{r['docs_with_edges']}/{r['total_docs']} docs connected, "
            f"{r['docs_capped']} docs hit the 8-edge cap"
        )
    elif cmd == "stats":
        stats()
    elif cmd == "sample":
        sample(int(argv[1]) if len(argv) > 1 else 10)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
