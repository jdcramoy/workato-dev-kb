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
# Scout-expanded 2026-10-10: llms.txt workflow/API sites + the RSS/Atom blog corpus.
CORE_SOURCES = {"docs", "blog", "changelog", "inspiration", "github", "eip", "claude",
                "temporal", "n8n", "prefect", "restate", "mcp", "rss", "webcrawl", "camel", "slack",
                "workday", "atlassian",
                # wave 5 (2026-10-15): HF library docs, Papers-with-Code
                # dumps, API catalogs (apis.guru / terraform registry / figshare
                # computing-sciences) — llms_scout is a REPORT step, not core
                # (a directory repo going down must not fail the nightly floor)
                "hf-datasets", "hf-hub", "hf-transformers", "hf-diffusers", "hf-peft",
                "hf-accelerate", "pwc", "apis-guru", "terraform-registry", "figshare"}


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

    from . import atlassian_source, camel_source, contextualize, docs_source, eip_source, github_source, html_source, links, llms_source, rss_source, wp_source, workday_source
    try:  # wave-5 sources: record an error, never kill the nightly on import
        from . import apis_guru_source, figshare_source, llms_scout, pwc_source, terraform_source
    except ImportError as e:
        errors.append(f"wave-5 source import: {e}")
        apis_guru_source = figshare_source = llms_scout = pwc_source = terraform_source = None
    from .entropy import score as entropy_score
    from .funnel import build as funnel_build
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
    # scout-expanded llms.txt sites (2026-10-10): each is its own core step so
    # the nightly floor catches a single site going down
    for _site in ("temporal", "n8n", "prefect", "restate", "mcp", "slack"):
        try_step(_site, llms_source.sync, _site)
    # wave 5: Hugging Face library docs (llms.txt + .md twins, version-stripped)
    for _site in ("hf-datasets", "hf-hub", "hf-transformers", "hf-diffusers", "hf-peft", "hf-accelerate"):
        try_step(_site, llms_source.sync, _site)
    try_step("rss", rss_source.sync)
    # scout wave 2 (2026-10-11): static-HTML books/essays (SRE books, course
    # notes, Jepsen, Kimball, distill, ...) via the generic link-walk/sitemap crawler
    try_step("webcrawl", html_source.sync)
    try_step("camel", camel_source.sync)
    # workday developer site: llms.txt docs + REST specs/
    # changelogs + SOAP catalog + GraphQL schema + tenant WQL alias export
    try_step("workday", workday_source.sync)
    try_step("atlassian", atlassian_source.sync)
    # wave-5 catalogs + dumps: PwC data dumps, API catalogs.
    # Each guarded — a module that failed to import records an error, not a crash.
    if pwc_source is not None:
        try_step("pwc", pwc_source.sync)
    if apis_guru_source is not None:
        try_step("apis-guru", apis_guru_source.sync)
    if terraform_source is not None:
        try_step("terraform-registry", terraform_source.sync)
    if figshare_source is not None:
        try_step("figshare", figshare_source.sync)
    # llms.txt directory scout (wave 5): refreshes the candidate-site queue
    # from the two directory repos — REPORT step, never core (the human works the
    # queue in waves; auto-ingest is deliberately out of scope)
    if llms_scout is not None:
        try_step("llms-scout", llms_scout.sync)
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
    # MRL coarse layer: re-embed doc contexts for newly contextualized docs.
    # Was manual-only until 2026-10-10 (audit found it never refreshed).
    try_step("funnel", funnel_build)
    # entity edges read the freshly-built chunks table; must run after index
    if edges_mod is not None:
        try_step("edges", edges_mod.build)
    try_step("pmi", pmi_build)
    try_step("entropy", entropy_score)

    # fractal projection: rebuild incrementally against the fresh index. Was
    # NOT in the chain until 2026-10-11 — the projection silently drifted a
    # full day behind kb.db and the audit scored stale rungs. Incremental
    # (per-source sha freshness); the audit's weekly pass can force --full.
    try:
        from .fractal import build as fractal_build

        try_step("fractal", fractal_build)
    except Exception as e:  # noqa: BLE001 - record, never kill the nightly
        errors.append(f"fractal import: {e}")

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
    # scoped runs get a .scoped suffix so they never clobber the nightly full
    # report for the same date (both used to write {date}.json — the full
    # reports for 10-09 and 10-10 were lost to exactly this)
    fname = f"{today}.json" if scope == "full" else f"{today}.scoped.json"
    (diff_dir / fname).write_text(json.dumps(report, indent=2))



    print(json.dumps(report, indent=2))
    if floor_failures:
        # loud failure AFTER the report + bus alert are on disk
        raise SystemExit(1)






if __name__ == "__main__":
    main()
