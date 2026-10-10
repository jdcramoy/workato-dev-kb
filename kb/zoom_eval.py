"""zoom_eval.py — token-cost evaluation of fractal zoom retrieval.

Tests the claim: "zoom retrieval answers memory questions at 10-30x less
context (300-500 tokens vs 2-10K for a whole file)."

For each probe question it replicates zoom's coarse-first FTS ranking
(exact same match syntax and bm25 weights as kb/fractal.py:zoom, queried
READ-ONLY so this never collides with a build in flight), picks the answer
at the coarsest level that matches well, and measures:

  - tokens of the returned context (chars/4)
  - the factlet level used (0 topic / 1 file / 2 section / 3 chunk)
  - whether the answer is section-or-better (the cheap path, level <= 2)
  - the baseline cost of returning the whole level-1 file

Usage:
  python3 -m kb.zoom_eval --before   # capture broken state -> zoom-costs-before.json
  python3 -m kb.zoom_eval --after    # rerun after rung repair -> zoom-costs-after.json
                                     # + before/after comparison table
  python3 -m kb.zoom_eval --json     # print results JSON to stdout
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .fractal import FACTLET_DB, LEVELS

EVAL_DIR = Path.home() / ".claude/workato-kb/eval"
BEFORE_PATH = EVAL_DIR / "zoom-costs-before.json"
AFTER_PATH = EVAL_DIR / "zoom-costs-after.json"
KB_DB = FACTLET_DB.parent / "kb.db"
KT = Path.home() / ".claude/knowledge"

# Threshold: if level-2 factlets number this many at --before time, the rung
# repair has landed (or mostly landed) and the "before" capture is polluted.
RACE_THRESHOLD = 2000
# Sanity floor: tonight's pre-repair count was 43 level-2 factlets across
# 10,548 files. Anything far above that is partial-repair state.
PRE_REPAIR_L2 = 43

# 25 deterministic probe questions spanning the corpus: Workato connectors,
# MCP/Agent Studio, EIP patterns, changelog features, platform mechanics.
PROBES = [
    "Slack bot connection custom OAuth profile",
    "on-prem agent group whitelisted IPs",
    "connector SDK methods execute",
    "lookup table row limit",
    "data table relation column",
    "MCP server tool assignment",
    "genie knowledge base attach",
    "VUA end user connection",
    "circuit breaker pattern",
    "saga pattern distributed transaction",
    "event sourcing vs message bus",
    "webhook trigger signature verification",
    "API platform endpoint authentication",
    "workflow apps connector",
    "data pipeline cross-account",
    "new connectors October changelog",
    "trigger condition debounce",
    "monolithic to microservices migration",
    "message queue ordering guarantee",
    "dead letter queue handling",
    "recipe import manifest export",
    "workday Get_Workers security grant",
    "salesforce bulk action batch",
    "recipe function error handling retry",
]


def _tokens(chars: int) -> int:
    return chars // 4


def _connect_ro() -> sqlite3.Connection:
    """Read-only connection — zoom_eval never writes to fractal.db.

    A rung-repair build may hold the write lock while we read, so set a
    generous busy timeout rather than failing on a transient lock."""
    db = sqlite3.connect(f"file:{FACTLET_DB}?mode=ro", uri=True, timeout=60)
    db.execute("PRAGMA busy_timeout=60000")
    return db


def _env_snapshot(db: sqlite3.Connection) -> dict:
    counts = {r[0]: r[1] for r in db.execute(
        "SELECT level, COUNT(*) FROM factlets GROUP BY level ORDER BY level")}
    built = [r[0] for r in db.execute("SELECT MAX(built_at) FROM build_meta")]
    total_files = counts.get(1, 0)
    l2 = counts.get(2, 0)
    files_with_sections = db.execute(
        "SELECT COUNT(DISTINCT parent) FROM factlets WHERE level=2 AND parent IS NOT NULL"
    ).fetchone()[0]
    return {
        "level_counts": counts,
        "total_level1_files": total_files,
        "level2_sections": l2,
        "files_with_sections": files_with_sections,
        "files_with_sections_pct": round(100 * files_with_sections / total_files, 1)
        if total_files else 0.0,
        "build_meta_last_built": built[0] if built else None,
        "raced": l2 >= RACE_THRESHOLD,
        "partial_repair_seen": l2 > PRE_REPAIR_L2,
    }


def _fts_rows(db: sqlite3.Connection, topic: str, limit: int = 30,
              exclude_levels: tuple[int, ...] = ()) -> list[dict]:
    """Replicate zoom's exact ranking: OR-joined quoted tokens, bm25 with
    title weight 8.0 / text weight 1.0, ascending (best first)."""
    tokens = re.findall(r"\w{2,}", topic.lower())
    match = " OR ".join(f'"{t}"' for t in tokens) or '""'
    excl = ""
    if exclude_levels:
        excl = " AND f.level NOT IN (%s)" % ",".join(str(x) for x in exclude_levels)
    rows = db.execute(
        "SELECT f.level, f.title, f.text, f.source, f.parent, f.id, "
        "bm25(factlets_fts, 8.0, 1.0) AS score "
        "FROM factlets_fts ft JOIN factlets f ON f.rowid = ft.rowid "
        "WHERE factlets_fts MATCH ?" + excl + " ORDER BY score LIMIT ?",
        (match, limit),
    ).fetchall()
    return [
        {"level": r[0], "level_name": LEVELS.get(r[0], str(r[0])),
         "title": r[1], "text": r[2], "source": r[3], "parent": r[4],
         "id": r[5], "score": r[6]}
        for r in rows
    ]


def _baseline_file_chars(db: sqlite3.Connection, source: str | None,
                         fallback_text: str) -> int:
    """Full-text chars of the whole level-1 file (pre-fractal baseline).

    Resolution order:
      1. knowledge-tree file on disk (sources like domains/x.md)
      2. kb.db docs row matched on URL/path suffix -> sum of its chunk text
      3. fallback: the factlet's own text (8000-char cap — understates the
         baseline, so any ratio measured against it is conservative)
    """
    if source:
        src = source.removesuffix("/")
        # 1. local knowledge-tree / memory / skill file
        if src.endswith(".md"):
            for root in (KT, Path.home() / ".claude"):
                p_f = root / src
                if p_f.is_file():
                    try:
                        return len(p_f.read_text(errors="replace"))
                    except OSError:
                        pass
        # 2. kb.db doc by URL/path suffix — take the max of every stored text
        #    measure (chunk text, chunk context, doc llm_context) so a sparse
        #    chunk layout can't undersell the whole file
        if KB_DB.is_file():
            tail = re.sub(r"^https?://", "", src)
            tail = tail.split("?", 1)[0].split("#", 1)[0]
            hit = sqlite3.connect(f"file:{KB_DB}?mode=ro", uri=True).execute(
                "SELECT MAX(x.v) FROM docs d JOIN ("
                " SELECT d2.doc_id, SUM(LENGTH(c.text)) v FROM docs d2"
                " JOIN chunks c ON c.doc_id = d2.doc_id GROUP BY d2.doc_id"
                " UNION SELECT d3.doc_id, LENGTH(d3.llm_context) FROM docs d3"
                ") x ON x.doc_id = d.doc_id "
                "WHERE d.url LIKE '%' || ? OR d.path LIKE '%' || ?",
                (tail, tail),
            ).fetchone()
            if hit and hit[0]:
                return int(hit[0])
    return len(fallback_text)


def _eval_one(db: sqlite3.Connection, question: str,
              exclude_levels: tuple[int, ...] = ()) -> dict:
    rows = _fts_rows(db, question, exclude_levels=exclude_levels)
    if not rows:
        return {"question": question, "answered": False, "answer_level": None,
                "answer_level_name": None, "answer_chars": 0, "answer_tokens": 0,
                "baseline_chars": 0, "baseline_tokens": 0, "ratio": None,
                "cheap_path": False}
    best = rows[0]
    # coarsest level whose best factlet matches well: its bm25 score must be
    # within MARGIN of the global best match (bm25 is more-negative-is-better)
    MARGIN = 3.0
    best_score = best["score"]
    chosen = None
    for lvl in (0, 1, 2, 3):
        cands = [r for r in rows if r["level"] == lvl and r["score"] <= best_score + MARGIN]
        if cands:
            chosen = [cands[0]]
            answer_level = lvl
            break
    if chosen is None:
        chosen = [best]
        answer_level = best["level"]
    answer_chars = sum(len(c["text"]) for c in chosen)
    # baseline = whole file behind the answer (its level-1 parent / its source)
    base_source = best["source"] if best["level"] <= 1 else best["source"]
    parent_row = None
    if best["parent"]:
        parent_row = db.execute(
            "SELECT text, source FROM factlets WHERE id = ?", (best["parent"],)
        ).fetchone()
    base_text = parent_row[0] if parent_row else best["text"]
    base_source = parent_row[1] if parent_row else base_source
    baseline_chars = _baseline_file_chars(db, base_source, base_text)
    ratio = round(baseline_chars / answer_chars, 1) if answer_chars else None
    return {
        "question": question,
        "answered": True,
        "answer_level": answer_level,
        "answer_level_name": LEVELS.get(answer_level, str(answer_level)),
        "answer_chars": answer_chars,
        "answer_tokens": _tokens(answer_chars),
        "baseline_chars": baseline_chars,
        "baseline_tokens": _tokens(baseline_chars),
        "ratio": ratio,
        "cheap_path": answer_level <= 2,
        "matched_titles": [c["title"][:60] for c in chosen],
    }


def run_eval_with_retry(reconstruct_before: bool = False) -> dict:
    """run_eval with a bounded retry — a repair build may hold the write lock
    for minutes at a time while it writes tens of thousands of factlets."""
    import time

    for attempt in range(30):
        try:
            return run_eval(reconstruct_before=reconstruct_before)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or attempt == 29:
                raise
            import sys
            print(f"[zoom_eval] fractal.db locked (repair build in flight); "
                  f"retry {attempt + 1}/30 in 60s", file=sys.stderr, flush=True)
            time.sleep(60)
    raise RuntimeError("unreachable")


def run_eval(reconstruct_before: bool = False) -> dict:
    db = _connect_ro()
    try:
        env = _env_snapshot(db)
        exclude: tuple[int, ...] = ()
        if reconstruct_before:
            # Pre-repair reconstruction: the repair only ADDED factlets
            # (level 0/2; chunks unchanged, level-1 +27 files). Excluding
            # levels 0 and 2 reproduces the pre-repair retrieval with the
            # same eval logic, so before/after differ only in rung coverage.
            exclude = (0, 2)
            env["reconstructed"] = True
            env["reconstruction_note"] = (
                "--before raced the repair: fractal.db already holds the repaired "
                f"rungs (level-2 count {env['level_counts'].get(2, 0)} >= "
                f"{RACE_THRESHOLD}). 'before' numbers below exclude level-0 and "
                "level-2 factlets, approximately reproducing the pre-repair "
                "retrieval (the repair also added ~2% level-1 files and ~4% "
                "level-3 chunks; rung coverage is the dominant difference). "
                f"build_meta last built: {env['build_meta_last_built']}.")
        results = [_eval_one(db, q, exclude_levels=exclude) for q in PROBES]
    finally:
        db.close()
    answered = [r for r in results if r["answered"]]
    ratios = [r["ratio"] for r in answered if r["ratio"]]
    tokens_list = [r["answer_tokens"] for r in answered]
    base_list = [r["baseline_tokens"] for r in answered]
    section_answers = [r for r in answered if r["answer_level"] == 2]
    out = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "probe_count": len(PROBES),
        "claim": "zoom retrieval answers at 10-30x less context "
                 "(300-500 tokens vs 2-10K whole file)",
        "env": env,
        "results": results,
        "summary": {
            "median_answer_tokens": statistics.median(tokens_list) if tokens_list else 0,
            "median_baseline_tokens": statistics.median(base_list) if base_list else 0,
            "pct_answered_at_section_or_better": round(
                100 * sum(1 for r in answered if r["cheap_path"]) / len(answered), 1)
            if answered else 0.0,
            "section_answer_count": len(section_answers),
            "median_tokens_at_section_level": statistics.median(
                [r["answer_tokens"] for r in section_answers]) if section_answers else None,
            "median_ratio_at_section_level": round(statistics.median(
                [r["ratio"] for r in section_answers if r["ratio"]]), 1)
            if section_answers else None,
            "median_zoom_vs_file_ratio": round(statistics.median(ratios), 1) if ratios else None,
            "min_ratio": min(ratios) if ratios else None,
            "max_ratio": max(ratios) if ratios else None,
        },
    }
    return out


def _print_table(out: dict, label: str) -> None:
    env = out["env"]
    s = out["summary"]
    print(f"\n== zoom-cost eval ({label}) — captured {out['captured_at']} ==")
    print(f"factlets L0/L1/L2/L3: {env['level_counts']}"
          f"  (sections on {env['files_with_sections_pct']}% of {env['total_level1_files']} files)")
    print(f"build_meta last built: {env['build_meta_last_built']}")
    if env.get("reconstructed"):
        print("RECONSTRUCTED pre-repair retrieval (repair had already landed):")
        print(f"  {env['reconstruction_note']}")
    elif env["raced"]:
        print("RACED: level-2 count >= threshold — rung repair already landed; "
              "this is NOT a clean pre-repair capture.")
    elif env["partial_repair_seen"]:
        print(f"PARTIAL REPAIR in flight: level-2 count is {env['level_counts'].get(2, 0)} "
              f"vs {PRE_REPAIR_L2} at discovery — treat numbers as mid-repair, not clean 'before'.")
    print(f"  median tokens/question : {s['median_answer_tokens']:.0f}")
    print(f"  median whole-file cost : {s['median_baseline_tokens']:.0f}")
    print(f"  % section-or-better   : {s['pct_answered_at_section_or_better']}%")
    if s.get("median_tokens_at_section_level") is not None:
        print(f"  section-level answers  : {s['section_answer_count']} probes, "
              f"median {s['median_tokens_at_section_level']:.0f} tok, "
              f"median ratio {s['median_ratio_at_section_level']}x")
    print(f"  zoom vs file ratio     : {s['median_zoom_vs_file_ratio']}x "
          f"(min {s['min_ratio']}, max {s['max_ratio']})")
    print(f"  claim band (10-30x, 300-500 tok): "
          f"{'PLAUSIBLE' if 300 <= s['median_answer_tokens'] <= 500 else 'NOT YET in band'}"
          f" on median tokens")


def _print_compare(before: dict, after: dict) -> None:
    b, a = before["summary"], after["summary"]
    rows = [
        ("median tokens/question", f"{b['median_answer_tokens']:.0f}", f"{a['median_answer_tokens']:.0f}"),
        ("median whole-file cost", f"{b['median_baseline_tokens']:.0f}", f"{a['median_baseline_tokens']:.0f}"),
        ("% answered section-or-better", f"{b['pct_answered_at_section_or_better']}%", f"{a['pct_answered_at_section_or_better']}%"),
        ("zoom vs file cost ratio", f"{b['median_zoom_vs_file_ratio']}x", f"{a['median_zoom_vs_file_ratio']}x"),
    ]
    print("\n== before/after: rung repair ==")
    print(f"{'metric':34} {'before':>10} {'after':>10}")
    for name, bv, av in rows:
        print(f"{name:34} {bv:>10} {av:>10}")
    print(f"\nper-question detail (after):")
    for r in after["results"]:
        lvl = r["answer_level_name"] or "none"
        flag = "cheap" if r["cheap_path"] else "expensive"
        print(f"  L{r['answer_level']} {lvl:8} {r['answer_tokens']:>5} tok "
              f"vs {r['baseline_tokens']:>6} tok = {r['ratio']}x  [{flag}]  {r['question'][:52]}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="kb.zoom_eval", description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--before", action="store_true",
                   help="capture the current (pre-repair) state")
    g.add_argument("--after", action="store_true",
                   help="rerun post-repair and print the before/after table")
    g.add_argument("--json", action="store_true", help="print results JSON to stdout")
    args = ap.parse_args()

    out = run_eval_with_retry()

    if args.json:
        print(json.dumps(out, indent=2))
        return

    if args.before:
        env0 = out["env"]
        raced = env0.get("level_counts", {}).get(2, 0) >= RACE_THRESHOLD
        if raced:
            # repair already landed: rerun as a pre-repair reconstruction
            out = run_eval_with_retry(reconstruct_before=True)
            out["env"]["raced"] = True
            out["env"]["literal_capture"] = (
                "literal pre-repair read at 2026-10-10T04:25:04Z (mid-repair, "
                "level-2=392 of 10,548 files, build_meta last built "
                "2026-10-08 16:04:44): median 1542 tokens/question, 32.0% "
                "section-or-better, 1.6x zoom-vs-file (median whole-file 2639 tok). "
                "Superseded below by the reconstructed pre-repair numbers using "
                "the same eval logic as --after.")
        EVAL_DIR.mkdir(parents=True, exist_ok=True)
        BEFORE_PATH.write_text(json.dumps(out, indent=2))
        _print_table(out, "BEFORE")
        print(f"saved -> {BEFORE_PATH}")
        return

    # --after
    if not BEFORE_PATH.exists():
        raise SystemExit(f"no --before capture at {BEFORE_PATH}; run --before first")
    before = json.loads(BEFORE_PATH.read_text())
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    AFTER_PATH.write_text(json.dumps(out, indent=2))
    _print_table(out, "AFTER")
    _print_compare(before, out)
    print(f"saved -> {AFTER_PATH}")


if __name__ == "__main__":
    main()
