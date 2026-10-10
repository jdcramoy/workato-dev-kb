"""Eval harness for the RAG++ knowledge base (recall/MRR over a fixed query set).

Query set lives at ~/.claude/workato-kb/eval/queries.jsonl — one JSON object per
line: {"qid", "query", "expect_urls", "slice"} where slice is "in-source"
(single-doc queries synthesized from corpus titles) or "cross-source"
(hand-authored multi-source topics, expectations = 2+ urls from different
sources).

Determinism: sampling is seeded, template choice is keyed to a crc32 of the url
(stable even if the candidate pool shifts), and the generated set is stored.
Regenerating replaces the stored set only via --regen.

    python3 -m kb.eval --regen                  # (re)build eval/queries.jsonl
    python3 -m kb.eval --run --tag baseline     # run all queries, write results-<tag>.json
    python3 -m kb.eval --compare a.json b.json  # delta table between two runs

Env flags in play (recorded in every result file): KB_EXPAND (query expansion),
plus the fixed config (k=10, rerank on).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

try:
    from .search import search
except ImportError:  # direct script execution fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from kb.search import search  # type: ignore

DATA_ROOT = Path(os.environ.get("KB_DATA_ROOT", os.path.expanduser("~/.claude/workato-kb")))
EVAL_DIR = DATA_ROOT / "eval"
QUERIES_PATH = EVAL_DIR / "queries.jsonl"
K = 10
SEED = 42

# Sources eligible per slice.
IN_SOURCES = ["docs", "claude", "changelog", "github", "eip"]
X_SOURCES = {"docs", "changelog", "blog", "eip", "claude"}

# per-source caps for the in-source slice (sums to 200)
IN_CAPS = {"docs": 90, "claude": 40, "changelog": 30, "github": 20, "eip": 10}

STOP_TITLES = {
    "untitled", "home", "introduction", "glossary", "faq", "faqs", "bibliography",
    "books", "index", "overview", "additional resources", "contributing", "changelog",
    "praise", "conclusion", "summary", "downloads", "about", "table of contents",
}

TITLE_PREFIXES = ("workato connectors - ", "workato connectors- ", "workato connector - ")

TEMPLATES = [
    "how do I use {t}",
    "{tl} documentation",
    "what is {t} and how does it work",
    "where can I find docs about {t}",
    "help me set up {t}",
    "{tl} example",
    "best practices for {t}",
    "how to configure {t} step by step",
]


def _clean_title(title: str) -> str:
    t = title
    for ent, ch in (("&#8217;", "'"), ("&#8216;", "'"), ("&#8211;", "-"), ("&#8212;", "-"), ("&amp;", "&")):
        t = t.replace(ent, ch)
    t = t.replace("’", "'").replace("–", "-").replace("—", "-")
    low = t.lower()
    for p in TITLE_PREFIXES:
        if low.startswith(p):
            t = t[len(p):]
            break
    t = re.sub(r"\(beta\)", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t


def _eligible(rec: dict) -> bool:
    title = (rec.get("title") or "").strip()
    if len(title) < 20:
        return False
    if title.lower() in STOP_TITLES:
        return False
    words = re.findall(r"[a-zA-Z]{3,}", title)
    if len(words) < 3:
        return False
    if not rec.get("url"):
        return False
    return True


def _norm_url(u: str) -> str:
    u = u.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = u.rstrip("/")
    if u.endswith(".md"):
        u = u[:-3]
    return u


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def _load_manifests(sources: list[str]) -> dict[str, list[dict]]:
    out = {}
    for src in sources:
        path = DATA_ROOT / "manifest" / f"{src}.jsonl"
        recs = []
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
        out[src] = recs
    return out


# ---------------------------------------------------------------------------
# In-source query synthesis
# ---------------------------------------------------------------------------

def _make_in_source(manifests: dict[str, list[dict]]) -> list[dict]:
    rng = random.Random(SEED)
    # deterministic candidate order: sorted by (sha256) then sample indices
    queries: list[dict] = []
    used_queries: set[str] = set()
    for src in IN_SOURCES:
        recs = [r for r in manifests[src] if _eligible(r)]
        recs.sort(key=lambda r: r.get("sha256", r.get("url", "")))
        cap = IN_CAPS[src]
        if len(recs) > cap:
            idx = sorted(rng.sample(range(len(recs)), cap))
            recs = [recs[i] for i in idx]
        # normalized-title -> urls map for collision allowance
        by_title: dict[str, list[str]] = {}
        for r in manifests[src]:
            t = _norm_title(_clean_title(r.get("title") or ""))
            if t:
                by_title.setdefault(t, []).append(r["url"])
        for rec in recs:
            t = _clean_title(rec["title"])
            # template picked by crc32(url): stable even if the pool shifts
            order = list(range(len(TEMPLATES)))
            order.sort(key=lambda i: (zlib.crc32(f"{rec['url']}:{i}".encode()), i))
            query = None
            for ti in order:
                cand = TEMPLATES[ti].format(t=t, tl=t.lower())
                key = _norm_title(cand)
                if key not in used_queries:
                    query = cand
                    used_queries.add(key)
                    break
            if query is None:
                continue
            expect = [rec["url"]]
            for u in by_title.get(_norm_title(t), []):
                if len(expect) >= 2:
                    break
                if u not in expect:
                    expect.append(u)  # same-source title collision
            queries.append({
                "qid": f"in-{len(queries)+1:03d}",
                "query": query,
                "expect_urls": expect,
                "slice": "in-source",
            })
    return queries


# ---------------------------------------------------------------------------
# Hard-negative sibling slice: same-parent docs with confusable titles. These
# are the measured failure class (recall 0.90 / MRR 0.65: found-but-misranked;
# sibling pages beat the target). Expectation = the EXACT target; confusable
# sibling urls are recorded for error analysis.
# ---------------------------------------------------------------------------

SIBLING_CAP = 40


def _make_siblings(manifests: dict[str, list[dict]]) -> list[dict]:
    from collections import defaultdict

    rng = random.Random(SEED + 1)
    by_parent: dict[str, list[dict]] = defaultdict(list)
    for r in manifests["docs"]:
        if not _eligible(r):
            continue
        parent = str(r.get("path", "")).rsplit("/", 1)[0]
        by_parent[parent].append(r)

    candidates: list[tuple[dict, list[dict]]] = []
    for parent, recs in by_parent.items():
        if len(recs) < 3:  # a real sibling group, not a two-file folder
            continue
        for rec in recs:
            t_tokens = set(_norm_title(_clean_title(rec.get("title") or "")).split())
            if len(t_tokens) < 2:
                continue
            confusables = [
                o for o in recs
                if o["url"] != rec["url"]
                and t_tokens & set(_norm_title(_clean_title(o.get("title") or "")).split())
            ]
            if confusables:
                candidates.append((rec, confusables))

    candidates.sort(key=lambda c: c[0].get("sha256", c[0]["url"]))
    if len(candidates) > SIBLING_CAP:
        idx = sorted(rng.sample(range(len(candidates)), SIBLING_CAP))
        candidates = [candidates[i] for i in idx]

    queries: list[dict] = []
    used: set[str] = set()
    for rec, confusables in candidates:
        t = _clean_title(rec["title"])
        order = list(range(len(TEMPLATES)))
        order.sort(key=lambda i: (zlib.crc32(f"sib:{rec['url']}:{i}".encode()), i))
        query = None
        for ti in order:
            cand = TEMPLATES[ti].format(t=t, tl=t.lower())
            if _norm_title(cand) not in used:
                query = cand
                used.add(_norm_title(cand))
                break
        if query is None:
            continue
        queries.append({
            "qid": f"sib-{len(queries)+1:03d}",
            "query": query,
            "expect_urls": [rec["url"]],
            "slice": "hard-sibling",
            "confusables": [c["url"] for c in confusables[:5]],
        })
    return queries



# Each entry: (query, [(source, needle), ...]). A needle matches when it appears
# in the doc's title OR url (lowercase). Expectations resolve to the first match
# per needle; a query is kept only if >=2 matches come from DIFFERENT sources.

# ---------------------------------------------------------------------------
# Cross-source hand-authored topics
# ---------------------------------------------------------------------------
# Each entry: (query, [(source, needle), ...]). A needle matches when it appears
# in the doc's title OR url (lowercase). Expectations resolve to the first match
# per needle; a query is kept only if >=2 matches come from DIFFERENT sources.

CROSS_SOURCE: list[tuple[str, list[tuple[str, str]]]] = [
    ("zoom meetings trigger setup and the zoom mcp server release",
     [("docs", "connectors/zoom.md"), ("changelog", "mcp-monday-outlook-calendar")]),
    ("salesforce connector actions and the salesforce bulk actions release",
     [("docs", "connectors/salesforce.md"), ("changelog", "announcing-salesforce-connector")]),
    ("on-prem files integration and the on-prem file update",
     [("docs", "connectors/on-prem-files.md"), ("changelog", "on-prem-file-major-update")]),
    ("rate limiting recipes and the polling consumer pattern",
     [("docs", "recipes/rate-limiting.md"), ("eip", "pollingconsumer")]),
    ("workato mcp server skills and the claude mcp connector",
     [("docs", "mcp-server-skills"), ("claude", "mcp-connector")]),
    ("pre-built mcp servers and remote mcp servers",
     [("changelog", "mcp-monday-26-pre-built-mcp-servers"), ("claude", "remote-mcp-servers")]),
    ("automation dashboard and operations hub dashboard faqs",
     [("changelog", "re-introducing-the-automation-dashboard"), ("docs", "admin-dashboard-faqs")]),
    ("workflow apps overview and building business automations with workflow apps",
     [("docs", "workflow-apps.md"), ("changelog", "build-seamless-business-automations")]),
    ("community connectors releases and the connector sdk quickstart",
     [("changelog", "community-connectors-whats-new"), ("docs", "developing-connectors/sdk")]),
    ("wire tap pattern and activity audit log streaming",
     [("eip", "wiretap"), ("docs", "activity-audit-log")]),
    ("message translator pattern and formula functions",
     [("eip", "messagetranslator"), ("docs", "formulas.md")]),
    ("remote mcp servers in claude and the airo mcp server",
     [("claude", "remote-mcp-servers"), ("docs", "airo/mcp.md")]),
    ("workbot for slack blog and slack connector setup in workato",
     [("blog", "workbot-for-slack"), ("docs", "connectors/slack.md")]),
    ("slack workbot blog posts and the slack gets sharper release",
     [("blog", "workbot-for-slack"), ("changelog", "mcp-monday-slack-gets-sharper")]),
    ("message batching api and product highlights release notes",
     [("claude", "message batches"), ("changelog", "product-highlights")]),
    ("lookup tables and picklist lookup support releases",
     [("docs", "features/lookup-tables.md"), ("changelog", "picklist-lookups")]),
    ("snowflake connector and java udfs for snowflake automations",
     [("docs", "connectors/snowflake.md"), ("changelog", "bringing-data-programmability")]),
    ("content filter pattern and api platform logs",
     [("eip", "contentfilter"), ("docs", "api-mgmt/api-logs")]),
    ("message channel pattern and webhook triggers",
     [("eip", "messagechannel"), ("docs", "connectors/opa-webhook.md")]),
    ("how claude tool use works and workato agentic skills",
     [("claude", "how tool use works"), ("docs", "agentic/skills")]),
    ("gmail connector and the gmail mcp monday release",
     [("docs", "workato connectors - gmail"), ("changelog", "mcp-monday-gmail")]),
    ("zoom connector best practices and the zoom mcp release",
     [("docs", "connectors/zoom/best-practices"), ("changelog", "mcp-monday-outlook-calendar")]),
    ("snowflake best practices guide and the snowflake connector",
     [("blog", "snowflake-best-practices"), ("docs", "connectors/snowflake.md")]),
    ("slack gets sharper release and the slack connector",
     [("changelog", "mcp-monday-slack-gets-sharper"), ("docs", "connectors/slack.md")]),
    ("agent studio and testing a skill without the genie",
     [("docs", "agentic/agent-studio.md"), ("changelog", "test-a-skill-w")]),
    ("genies scale to longer more ambitious work and agent studio",
     [("changelog", "genies-scale"), ("docs", "agentic/agent-studio.md")]),
    ("request reply pattern and the api platform",
     [("eip", "request-reply"), ("docs", "api-management.md")]),
    ("claude webhooks and workato webhook triggers",
     [("claude", "subscribe to webhooks"), ("docs", "connectors/opa-webhook.md")]),
]


def _resolve_cross(manifests: dict[str, list[dict]]) -> list[dict]:
    pool = {s: manifests[s] for s in X_SOURCES}
    queries: list[dict] = []
    for qi, (query, matchers) in enumerate(CROSS_SOURCE, 1):
        expect: list[str] = []
        matched_sources: set[str] = set()
        for src, needle in matchers:
            hit = next((r for r in pool[src]
                        if needle in (r.get("title") or "").lower()
                        or needle in (r.get("url") or "").lower()), None)
            if hit is None:
                continue
            if hit["url"] not in expect:
                expect.append(hit["url"])
            matched_sources.add(src)
        if len(matched_sources) < 2 or len(expect) < 2:
            print(f"[regen] WARN cross-source q{qi} unresolved ({len(matched_sources)} sources): {query!r}",
                  file=sys.stderr)
            continue
        queries.append({
            "qid": f"xs-{qi:03d}",
            "query": query,
            "expect_urls": expect,
            "slice": "cross-source",
        })
    return queries


# ---------------------------------------------------------------------------
# Regeneration + loading
# ---------------------------------------------------------------------------

def regen() -> int:
    manifests = _load_manifests(sorted(set(IN_SOURCES) | X_SOURCES))
    in_q = _make_in_source(manifests)
    xs_q = _resolve_cross(manifests)
    sib_q = _make_siblings(manifests)
    all_q = in_q + xs_q + sib_q
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with QUERIES_PATH.open("w") as f:
        for q in all_q:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(
        f"wrote {len(all_q)} queries -> {QUERIES_PATH} "
        f"(in-source={len(in_q)}, cross-source={len(xs_q)}, hard-sibling={len(sib_q)})"
    )
    return len(all_q)


def load_queries() -> list[dict]:
    if not QUERIES_PATH.exists():
        sys.exit(f"no query set at {QUERIES_PATH} — run: python3 -m kb.eval --regen")
    return [json.loads(l) for l in QUERIES_PATH.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# Run + metrics
# ---------------------------------------------------------------------------

def score_query(row: dict, got_urls: list[str]) -> dict:
    expect_n = [_norm_url(u) for u in row["expect_urls"]]
    got_n = [_norm_url(u) for u in got_urls]
    top = got_n[:K]
    hits = [u in top for u in expect_n]
    recall = sum(hits) / len(expect_n) if expect_n else 0.0
    mrr = 0.0
    for rank, u in enumerate(top, 1):
        if u in expect_n:
            mrr = 1.0 / rank
            break
    return {
        "qid": row["qid"],
        "query": row["query"],
        "slice": row["slice"],
        "expect_urls": row["expect_urls"],
        "top10_urls": got_urls[:K],
        "recall@10": round(recall, 4),
        "mrr@10": round(mrr, 4),
    }


def run(tag: str) -> Path:
    queries = load_queries()
    rows: list[dict] = []
    for i, row in enumerate(queries, 1):
        err = None
        try:
            hits, meta = search(row["query"], k=K, rerank=True)
            got = [h.url for h in hits]
        except Exception as e:  # keep the run going; record the failure
            print(f"[run] ERROR q={row['qid']}: {e}", file=sys.stderr)
            got = []
            err = str(e)
        rows.append({**score_query(row, got), "error": err})
        if i % 25 == 0 or i == len(queries):
            print(f"  {i}/{len(queries)} queries scored", flush=True)

    def agg(rs: list[dict]) -> dict:
        n = len(rs) or 1
        zero = sum(1 for r in rs if r["recall@10"] == 0.0)
        return {
            "n": len(rs),
            "recall@10": round(sum(r["recall@10"] for r in rs) / n, 4),
            "mrr@10": round(sum(r["mrr@10"] for r in rs) / n, 4),
            "zero_recall_queries": zero,
        }

    summary = {
        "overall": agg(rows),
        "in-source": agg([r for r in rows if r["slice"] == "in-source"]),
        "cross-source": agg([r for r in rows if r["slice"] == "cross-source"]),
        "hard-sibling": agg([r for r in rows if r["slice"] == "hard-sibling"]),
    }
    result = {
        "tag": tag,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "k": K,
            "rerank": True,
            "kb_expand": os.environ.get("KB_EXPAND", ""),
            "seed": SEED,
            "queries_path": str(QUERIES_PATH),
            "queries_sha256": hashlib.sha256(QUERIES_PATH.read_bytes()).hexdigest()[:16],
        },
        "summary": summary,
        "rows": rows,
    }
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    out = EVAL_DIR / f"results-{tag}.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"\n== {tag} ==")
    for name in ("overall", "in-source", "cross-source", "hard-sibling"):
        s = summary[name]
        if s["n"] == 0:
            continue
        print(f"  {name:13s} n={s['n']:<4} Recall@10={s['recall@10']:.4f}  MRR@10={s['mrr@10']:.4f}  zero-recall={s['zero_recall_queries']}")
    print(f"wrote {out}")
    return out


def compare(a_path: str, b_path: str) -> None:
    a = json.loads(Path(a_path).read_text())
    b = json.loads(Path(b_path).read_text())
    print(f"comparing: {a_path} (baseline/left) vs {b_path} (right)\n")
    print(f"{'slice':13s} {'metric':10s} {'a':>8s} {'b':>8s} {'delta':>8s}")
    for name in ("overall", "in-source", "cross-source", "hard-sibling"):
        if name not in a["summary"] or name not in b["summary"]:
            continue
        for metric in ("recall@10", "mrr@10"):
            va = a["summary"][name][metric]
            vb = b["summary"][name][metric]
            print(f"{name:13s} {metric:10s} {va:8.4f} {vb:8.4f} {vb-va:+8.4f}")
    ra = {r["qid"]: r for r in a["rows"]}
    rb = {r["qid"]: r for r in b["rows"]}
    moved = []
    for qid, row_b in rb.items():
        row_a = ra.get(qid)
        if row_a and abs(row_b["recall@10"] - row_a["recall@10"]) > 1e-9:
            moved.append((qid, row_a["recall@10"], row_b["recall@10"]))
    if moved:
        print(f"\nper-query recall deltas ({len(moved)} changed):")
        for qid, va, vb in sorted(moved, key=lambda x: x[2] - x[1]):
            print(f"  {qid}: {va:.2f} -> {vb:.2f}  {rb[qid]['query'][:70]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="RAG++ eval harness")
    ap.add_argument("--regen", action="store_true", help="regenerate eval/queries.jsonl (replaces stored set)")
    ap.add_argument("--run", action="store_true", help="run every query through kb.search and score")
    ap.add_argument("--tag", default="run", help="tag for results-<tag>.json")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"), help="delta table between two result files")
    args = ap.parse_args()
    if args.regen:
        regen()
    if args.run:
        run(args.tag)
    if args.compare:
        compare(*args.compare)
    if not (args.regen or args.run or args.compare):
        ap.print_help()


if __name__ == "__main__":
    main()
