"""Cluster hub notes: one coarse topical entry-point doc per doc cluster.

Builds the fractal topic layer over the indexed corpus:
- One embedding per DOC = the indexer's own first-chunk embedding (already
  embed(context_header + chunk_ord=0 text)); stragglers are re-embedded with
  kb.indexer.get_model("embed").
- Clustering chain: hdbscan (pkg) -> sklearn HDBSCAN -> agglomerative
  (average linkage, unit-normalized vectors, distance-threshold sweep) ->
  MiniBatchKMeans with silhouette-chosen k. First method producing 30-150
  coherent clusters wins; coherence > coverage, so docs in no cluster (noise
  or clusters smaller than MIN_CLUSTER_SIZE) are orphans, never force-assigned.
- Per cluster: one hub note at corpus/hubs/hub-<slug>.md with a deterministic
  within-cluster-vs-rest TF-IDF label, templated 2-sentence summary, and up to
  30 relative member links. No LLM calls anywhere.
- manifest/hubs.jsonl in the standard {url, path, title, sha256} shape so the
  indexer can ingest hubs as a normal `hubs` source later. Stale hub files are
  deleted on rebuild (idempotent).

Usage:  python3 -m kb.hubs build
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import unicodedata
from datetime import date

import numpy as np

from .common import DATA_ROOT, save_manifest
from .indexer import get_db, get_model

HUB_SOURCE = "hubs"
HUB_DIR = DATA_ROOT / "corpus" / "hubs"

MIN_CLUSTER_SIZE = 3   # clusters smaller than this dissolve into orphans
MAX_HUB_SIZE = 200     # clusters larger than this dissolve into orphans too:
                       # a 2000-doc "hub" is soup, not an entry point
MIN_CLUSTERS = 30
MAX_CLUSTERS = 150
SHOW_MEMBERS = 30
HDBSCAN_MIN_CLUSTER = 5
# Euclidean on unit-normalized vectors: 0.62 ~ cos sim 0.81, 1.0 ~ cos sim 0.5.
# Sweep is wide because the corpus holds many small tight groups that only
# merge at high distances.
DIST_THRESHOLDS = [0.62, 0.70, 0.80, 0.85, 0.90, 0.95, 1.00, 1.10, 1.20]
EMBED_BATCH = 128

# Corpus boilerplate that is never cluster-distinctive (merged with sklearn's
# english stop list at TF-IDF time).
EXTRA_STOP = {
    "workato", "main", "untitled", "page", "pages", "doc", "docs", "document",
    "section", "step", "steps", "use", "using", "used", "set", "setting",
    "settings", "guide", "overview", "index", "readme", "recipe", "recipes",
    "connect", "connected", "connection",
}

# Terms that must keep acronym casing in labels.
ACRONYMS = {
    "api", "apis", "mcp", "crm", "erp", "hr", "it", "ai", "ml", "saas", "oauth",
    "sso", "http", "https", "sql", "db", "aws", "gcp", "pdf", "url", "uri",
    "json", "xml", "csv", "rest", "sdk", "ui", "ux", "id", "ids", "js", "eip",
    "vm", "vpn", "tcp", "ip", "rbac", "sla", "kpi", "roi", "vat", "us", "uk",
    "eu", "apac", "nlp", "llm", "gpt", "ops", "devops", "bi", "etl", "gdpr",
    "pii", "iam", "b2b", "b2c", "okr", "rfc", "faas", "paas", "iaas",
    "sap", "rpa", "uat", "qa", "sre", "kb", "netsuite", "sfdc",
}


# ------------------------------------------------------------------ doc load

def _is_hub_path(path: str) -> bool:
    """Self-pollution guard: hubs are never clustered with members."""
    return "hubs" in path.split("/")


def _load_docs() -> list[dict]:
    """Metadata for every indexed non-hub doc (first-chunk text for labels)."""
    db = get_db()
    rows = db.execute(
        """
        SELECT d.doc_id, d.url, d.path, d.title, d.source, d.section, d.llm_context, c.text
        FROM docs d
        LEFT JOIN chunks c ON c.doc_id = d.doc_id AND c.chunk_ord = 0
        """
    ).fetchall()
    db.close()
    docs = []
    for doc_id, url, path, title, source, section, llm_ctx, chunk in rows:
        if _is_hub_path(path):
            continue
        docs.append(
            {
                "doc_id": doc_id,
                "url": url,
                "path": path,
                "title": title or path,
                "source": source,
                "section": section or "",
                "llm_context": (llm_ctx or "").strip(),
                "chunk": chunk or "",
            }
        )
    return docs


def _doc_embed_text(doc: dict) -> str:
    """Mirror kb.indexer.build's context_header + chunk assembly exactly."""
    sec = doc["section"] or doc["source"]
    context_header = (
        f"[source: {doc['source']} | section: {sec} | page: {doc['title']} | url: {doc['url']}]"
    )
    if doc["llm_context"]:
        context_header = (
            f"[{doc['source']} | {sec} | {doc['title']}] {doc['llm_context']} (url: {doc['url']})"
        )
    return f"{context_header}\n\n{doc['chunk']}"


def _doc_vectors(docs: list[dict]) -> np.ndarray:
    """Doc embedding matrix: reuse the indexer's first-chunk vectors (which are
    embed(context_header + first chunk)); re-embed only docs whose vec row is
    missing, batched on the same bge model."""
    db = get_db()
    row = db.execute("SELECT embedding FROM chunks_vec LIMIT 1").fetchone()
    dim = len(row[0]) // 4 if row else 768

    vecs = np.zeros((len(docs), dim), dtype=np.float32)
    missing: list[int] = []
    for i, doc in enumerate(docs):
        r = db.execute(
            """
            SELECT v.embedding FROM chunks c
            JOIN chunks_vec v ON v.chunk_id = c.chunk_id
            WHERE c.doc_id = ? AND c.chunk_ord = 0
            """,
            (doc["doc_id"],),
        ).fetchone()
        if r:
            vecs[i] = np.array(struct.unpack(f"{dim}f", r[0]), dtype=np.float32)
        else:
            missing.append(i)
    db.close()

    if missing:
        print(f"  re-embedding {len(missing)} docs missing first-chunk vectors ...")
        model = get_model("embed")
        texts = [_doc_embed_text(docs[i]) for i in missing]
        for s in range(0, len(texts), EMBED_BATCH):
            batch = texts[s : s + EMBED_BATCH]
            emb = model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
            vecs[s : s + len(batch)] = np.asarray(emb, dtype=np.float32)
            print(f"  embedded {min(s + EMBED_BATCH, len(texts))}/{len(texts)}", flush=True)
    return vecs


# ----------------------------------------------------------------- clustering

def _clusters_in_range(labels: np.ndarray) -> int:
    """Count clusters with at least MIN_CLUSTER_SIZE members."""
    labels = np.asarray(labels)
    counts = np.bincount(labels[labels >= 0])
    return int((counts >= MIN_CLUSTER_SIZE).sum())


def _try_hdbscan(X: np.ndarray, pkg: bool) -> np.ndarray | None:
    try:
        if pkg:
            import hdbscan  # type: ignore

            labels = hdbscan.HDBSCAN(
                min_cluster_size=HDBSCAN_MIN_CLUSTER, metric="euclidean"
            ).fit_predict(X)
        else:
            from sklearn.cluster import HDBSCAN as SKHDBSCAN

            labels = SKHDBSCAN(
                min_cluster_size=HDBSCAN_MIN_CLUSTER,
                min_samples=3,
                metric="euclidean",
            ).fit_predict(X)
        n = _clusters_in_range(labels)
        print(f"  HDBSCAN ({'hdbscan pkg' if pkg else 'sklearn'}): {n} clusters in range")
        return labels if MIN_CLUSTERS <= n <= MAX_CLUSTERS else None
    except ImportError:
        if pkg:
            print("  hdbscan package not installed, falling back to sklearn HDBSCAN")
        return None


def _agglomerative(X: np.ndarray) -> tuple[np.ndarray, float]:
    """AgglomerativeClustering(n_clusters=None, linkage='average') with a
    distance-threshold sweep. The merge tree is computed ONCE (scipy average
    linkage == the sklearn agglomerative average-linkage tree over the same
    unit-normalized vectors) and cut at each threshold, so the sweep costs one
    O(n^2) pass instead of one per threshold."""
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import pdist

    D = pdist(X, metric="euclidean")  # unit vectors: euclid d <-> cos sim
    Z = linkage(D, method="average")
    best: tuple[int, float, np.ndarray] | None = None  # (dist-to-range, t, labels)
    for t in DIST_THRESHOLDS:
        labels = fcluster(Z, t=t, criterion="distance") - 1  # 0-based
        n = _clusters_in_range(labels)
        print(f"  agglomerative threshold {t}: {n} clusters in range")
        if MIN_CLUSTERS <= n <= MAX_CLUSTERS:
            # tightest in-range threshold wins (coherence over coverage)
            return labels, t
        dist = min(abs(n - MIN_CLUSTERS), abs(n - MAX_CLUSTERS))
        if best is None or dist < best[0]:
            best = (dist, t, labels)
    assert best is not None
    return best[2], best[1]


def _kmeans(X: np.ndarray) -> np.ndarray:
    from sklearn.cluster import MiniBatchKMeans
    from sklearn.metrics import silhouette_score

    rng = np.random.default_rng(42)
    sample = rng.choice(len(X), size=min(4000, len(X)), replace=False)
    best: tuple[float, np.ndarray] | None = None
    for k in (40, 60, 80, 100, 120):
        labels = MiniBatchKMeans(n_clusters=k, random_state=42, batch_size=2048).fit_predict(X)
        sil = silhouette_score(X[sample], labels[sample])
        print(f"  kmeans k={k}: silhouette {sil:.3f}")
        if best is None or sil > best[0]:
            best = (sil, labels)
    assert best is not None
    return best[1]


def cluster_docs(X: np.ndarray) -> tuple[str, float, np.ndarray]:
    """Try the method chain in spec order; return (method, param, labels)."""
    labels = _try_hdbscan(X, pkg=True)
    if labels is not None:
        return "HDBSCAN (hdbscan package)", float(HDBSCAN_MIN_CLUSTER), labels

    labels = _try_hdbscan(X, pkg=False)
    if labels is not None:
        return "HDBSCAN (sklearn)", float(HDBSCAN_MIN_CLUSTER), labels

    labels, t = _agglomerative(X)
    n = _clusters_in_range(labels)
    if MIN_CLUSTERS <= n <= MAX_CLUSTERS:
        return (
            "AgglomerativeClustering (average linkage, unit-normalized, "
            f"distance_threshold={t})",
            t,
            labels,
        )

    return "MiniBatchKMeans (silhouette-chosen k)", 0.0, _kmeans(X)


# ------------------------------------------------------------------- labeling

def _smart_title(term: str) -> str:
    words = []
    for w in term.split():
        if w.lower() in ACRONYMS:
            words.append(w.upper())
        else:
            words.append(w[:1].upper() + w[1:])
    return " ".join(words)


def _slugify(label: str) -> str:
    slug = unicodedata.normalize("NFKD", label.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug[:60].strip("-") or "cluster"


def _label_clusters(
    docs: list[dict], labels: np.ndarray
) -> dict[int, tuple[str, list[int]]]:
    """cluster_id -> (label, member doc indices). Distinctive terms are the top
    within-cluster mean TF-IDF terms vs the rest of the corpus."""
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

    texts = [f"{d['title']}. {d['chunk'][:1500]}" for d in docs]
    tfidf = TfidfVectorizer(
        stop_words=list(sorted(ENGLISH_STOP_WORDS | EXTRA_STOP)),
        min_df=3,
        max_df=0.6,
        ngram_range=(1, 2),
        sublinear_tf=True,
        max_features=100_000,
    )
    M = tfidf.fit_transform(texts)
    terms = np.array(tfidf.get_feature_names_out())
    global_mean = np.asarray(M.mean(axis=0)).ravel()

    out: dict[int, tuple[str, list[int]]] = {}
    for cid in sorted(set(int(l) for l in labels if l >= 0)):
        idx = np.where(labels == cid)[0]
        if len(idx) < MIN_CLUSTER_SIZE:
            continue
        cm = np.asarray(M[idx].mean(axis=0)).ravel()
        score = cm - global_mean  # distinctiveness vs other clusters
        order = np.argsort(-score)
        picked: list[str] = []
        for j in order:
            t = str(terms[j])
            if score[j] <= 0 or len(t) < 3 or any(ch.isdigit() for ch in t):
                continue
            if any(t in p or p in t for p in picked):  # dedup sub/superstrings
                continue
            picked.append(t)
            if len(picked) == 5:
                break
        label = " ".join(_smart_title(t) for t in picked[:5]) if picked else "Cluster"
        out[cid] = (label, sorted(int(i) for i in idx))
    return out


# --------------------------------------------------------------- hub notes IO

def _frontmatter(title: str, url: str, n_members: int) -> str:
    return (
        "---\n"
        f'url: "{url}"\n'
        f'title: "{title}"\n'
        f'source: "{HUB_SOURCE}"\n'
        f"members: {n_members}\n"
        f'generated: "{date.today().isoformat()}"\n'
        "---\n"
    )


def _hub_body(label: str, docs: list[dict], member_idx: list[int]) -> str:
    members = sorted((docs[i] for i in member_idx), key=lambda d: d["title"].casefold())

    def esc(t: str) -> str:
        return t.replace("[", "(").replace("]", ")")

    shown = members[:SHOW_MEMBERS]
    rep = [esc(m["title"]) for m in shown[:3]]
    terms = " ".join(w.lower() for w in label.split()[:3])
    line2 = f", together covering {terms}"
    if len(rep) == 1:
        names = f'"{rep[0]}"'
    elif len(rep) == 2:
        names = f'"{rep[0]}" and "{rep[1]}"'
    else:
        names = f'"{rep[0]}", "{rep[1]}", and "{rep[2]}"'
    lines = [
        f"This hub groups {len(members)} related documents on {label.lower()}.",
        f"Representative members include {names}{line2}.",
        "",
        "## Members",
        "",
    ]
    for m in shown:
        # corpus-relative member path prefixed with corpus/ so the ../../ hop
        # from corpus/hubs/ resolves to the data-root/corpus dir layout
        lines.append(f"- [{esc(m['title'])}](../../corpus/{m['path']})")
    if len(members) > SHOW_MEMBERS:
        lines.append("")
        lines.append(f"+{len(members) - SHOW_MEMBERS} more not listed.")
    return "\n".join(lines) + "\n"


def build() -> dict:
    print("loading docs and doc vectors ...")
    docs = _load_docs()
    X = _doc_vectors(docs)
    X = X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)

    print(f"clustering {len(docs)} docs ...")
    method, param, labels = cluster_docs(X)
    clusters = _label_clusters(docs, labels)
    oversized = [cid for cid, (_, m) in clusters.items() if len(m) > MAX_HUB_SIZE]
    for cid in oversized:
        del clusters[cid]
    clustered_docs = sum(len(m) for _, m in clusters.values())
    orphans = len(docs) - clustered_docs
    if oversized:
        print(
            f"  dissolved {len(oversized)} oversized clusters (>{MAX_HUB_SIZE} docs) "
            f"into orphans: {orphans} total orphans"
        )

    sizes = sorted(len(m) for _, m in clusters.values())
    stats = {
        "method": method,
        "param": param,
        "clusters": len(clusters),
        "docs": len(docs),
        "clustered_docs": clustered_docs,
        "orphans": orphans,
        "size_min": sizes[0] if sizes else 0,
        "size_median": float(np.median(sizes)) if sizes else 0,
        "size_max": sizes[-1] if sizes else 0,
    }
    print(json.dumps(stats, indent=2))

    HUB_DIR.mkdir(parents=True, exist_ok=True)
    records: dict[str, dict] = {}
    used_slugs: set[str] = set()
    for cid, (label, member_idx) in sorted(clusters.items()):
        base = _slugify(label)
        slug, n = base, 1
        while slug in used_slugs:
            n += 1
            slug = f"{base}-{n}"
        used_slugs.add(slug)
        url = f"hubs://hub-{slug}"
        title = f"Hub: {label}"
        rel_path = f"hubs/hub-{slug}.md"
        text = _frontmatter(title, url, len(member_idx)) + "\n" + _hub_body(label, docs, member_idx)
        (DATA_ROOT / "corpus" / rel_path).write_text(text)
        records[url] = {
            "url": url,
            "path": rel_path,
            "title": title,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    # idempotent rebuild: delete stale hub files not in the current cluster set
    keep = set(records)  # by url
    removed = 0
    for f in sorted(HUB_DIR.glob("*.md")):
        fpath = "hubs/" + f.name
        stale = all(r["path"] != fpath for r in records.values())
        if stale:
            f.unlink()
            removed += 1

    save_manifest(HUB_SOURCE, records)
    print(
        f"wrote {len(records)} hub notes, removed {removed} stale files, "
        "manifest/hubs.jsonl saved"
    )

    examples = []
    for cid, (label, member_idx) in sorted(clusters.items())[:3]:
        members = sorted((docs[i]["title"] for i in member_idx), key=str.casefold)
        examples.append({"label": label, "top5": members[:5]})
    return {**stats, "examples": examples}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="cluster the corpus and write hub notes")
    args = ap.parse_args()
    if args.cmd == "build":
        result = build()
        print(json.dumps(result["examples"], indent=2))
