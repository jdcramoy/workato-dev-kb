"""Nightly orchestrator: sync all sources -> re-index changed docs -> diff report.

    python3 -m kb.sync                 # everything
    python3 -m kb.sync --no-embed      # crawl+chunk only
    python3 -m kb.sync --sources docs  # scoped run (single step; report marked "scoped")

Writes a diff report to ~/.claude/workato-kb/diffs/YYYY-MM-DD.json.

The report carries a "scope" field ("full" or "scoped") so a targeted single-step run
is never mistaken for a nightly full sync. Full runs enforce a source-count floor:
if any core source step is missing or errors (or total changed-count collapses to 0
against a nonzero previous run), the sync fails loudly — report is still written but
the process exits nonzero. This guards against
a silently shrunken corpus/index.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from .common import DATA_ROOT, fetch_cached

# True ingest sources. Post-processing steps (contextualize, index, pmi,
# entropy, lateral) are not counted;
# a full run MUST complete every one of these or the sync is considered failed.
CORE_SOURCES = {"docs", "blog", "changelog", "inspiration", "github", "eip", "claude"}


def _step_healthy(result) -> bool:
    """A step is healthy when it ran and did not record an error (skips count as ran)."""
    return isinstance(result, dict) and not result.get("error")


def _previous_report(today: str):
    """Most recent diff report strictly before today, for the collapse check."""
    diff_dir = DATA_ROOT / "diffs"
    if not diff_dir.is_dir():
        return None
    prior = sorted(p for p in diff_dir.glob("*.json") if p.stem < today)
    if not prior:
        return None
    try:
        return json.loads(prior[-1].read_text())
    except Exception:  # noqa: BLE001 - collapse check is best effort
        return None


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--sources", nargs="*", default=None)
    a = ap.parse_args()
    wanted = a.sources
    scope = "scoped" if wanted else "full"

    results: dict = {}
    errors: list[str] = []

    def try_step(name: str, fn, *args, **kwargs):
        if wanted and name not in wanted:
            return
        print(f"[sync] {name} ...", flush=True)
        try:
            results[name] = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - nightly job records, never dies silently
            errors.append(f"{name}: {e}")
            results[name] = {"error": str(e)}

    from . import contextualize, docs_source, eip_source, github_source, links, llms_source, wp_source
    from .entropy import score as entropy_score
    from .indexer import build
    from .pmi import build as pmi_build
    # optional graph-layer steps: a missing module records an error in the
    # report instead of crashing the nightly sync
    hubs_mod = edges_mod = None
    try:
        from . import hubs as hubs_mod  # noqa: PLC0415
    except ImportError as e:
        errors.append(f"hubs import: {e}")
    try:
        from . import edges as edges_mod  # noqa: PLC0415
    except ImportError as e:
        errors.append(f"edges import: {e}")

    try_step("docs", docs_source.sync)
    try_step("blog", wp_source.sync_blog)
    try_step("changelog", wp_source.changelog_incremental)
    try_step("inspiration", wp_source.sync_inspiration)
    try_step("github", github_source.sync)
    try_step("eip", eip_source.sync)
    try_step("claude", llms_source.sync, "claude")
    # link normalization: website-style .html / absolute docs links -> relative .md
    # links that resolve inside the corpus (Obsidian/md-graph edges). MUST run after
    # every source step or sources re-emit raw links; runs before index build.
    try_step("links", links.normalize)
    # cluster hubs -> corpus/hubs BEFORE index so hubs are ingested as a source
    # in the same sync (hubs must not cluster themselves: self-pollution guard)
    if hubs_mod is not None:
        try_step("hubs", hubs_mod.build)
    try_step("contextualize", contextualize.run, 400)
    try_step("index", build, with_embeddings=not a.no_embed)
    # entity edges read the freshly-built chunks table; must run after index
    if edges_mod is not None:
        try_step("edges", edges_mod.build)
    try_step("pmi", pmi_build)
    try_step("entropy", entropy_score)

    # concept-graph feedstock
    if not wanted or "lateral" in wanted:
        try:
            from .lateral import build as lateral_build

            results["lateral"] = lateral_build()
        except Exception as e:  # noqa: BLE001
            errors.append(f"lateral: {e}")

    today = dt.date.today().isoformat()

    # --- source-count floor (full runs only) ---------------------------------
    # Fails loudly instead of silently writing a shrunken corpus/index.
    floor_failures: list[str] = []
    if scope == "full":
        missing_core = sorted(CORE_SOURCES - {n for n, r in results.items() if _step_healthy(r)})
        if missing_core:
            floor_failures.append(
                f"source-count floor: core sources missing or failed: {missing_core}"
            )
        changed_total = sum(
            r.get("changed", 0)
            for r in results.values()
            if isinstance(r, dict) and not r.get("error")
        )
        prev = _previous_report(today)
        if prev is not None:
            prev_changed = sum(
                r.get("changed", 0)
                for r in prev.get("results", {}).values()
                if isinstance(r, dict) and not r.get("error")
            )
            if prev_changed > 0 and changed_total == 0:
                floor_failures.append(
                    f"collapse check: 0 docs changed across all sources vs {prev_changed} in the previous run"
                )
    report = {"date": today, "scope": scope, "results": results, "errors": errors}
    if floor_failures:
        report["floor_failures"] = floor_failures
    diff_dir = DATA_ROOT / "diffs"
    diff_dir.mkdir(parents=True, exist_ok=True)
    (diff_dir / f"{today}.json").write_text(json.dumps(report, indent=2))



    print(json.dumps(report, indent=2))
    if floor_failures:
        # loud failure AFTER the report + bus alert are on disk
        raise SystemExit(1)






if __name__ == "__main__":
    main()
