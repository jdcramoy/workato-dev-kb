"""Manual-import connect chain: Discover > import > connect.

The nightly sync (kb.sync) runs the full chain for its own sources. Any
content imported OUTSIDE the nightly — a crawled Confluence/Drive page, a
manual file drop, PDF extracts — must end with THIS chain, or the new docs
sit on disk unindexed, unlinked, and unreachable until the next nightly
backfills them. That backfill is a bug-shaped workflow; this command makes
connect the last step of every import, run at import time.

    python3 -m kb.ingest          # links -> index -> edges -> pmi -> entropy
    python3 -m kb.ingest --quick  # links -> index only (edges/pmi catch up nightly)

Expects the import already done the standard way: corpus file under
~/.claude/workato-kb/corpus/<source>/ with a {url, path, sha256, title, ...}
record in manifest/<source>.jsonl.

  python3 -m kb.ingest
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true", help="skip edges/pmi/entropy (nightly catches up)")
    a = ap.parse_args()

    from . import contextualize, links
    from .entropy import score as entropy_score
    from .indexer import build
    from .pmi import build as pmi_build

    results: dict = {}
    errors: list[str] = []

    def try_step(name, fn, *args, **kwargs):
        print(f"[ingest] {name} ...", flush=True)
        try:
            results[name] = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - report every step, never die mid-chain
            errors.append(f"{name}: {e}")
            results[name] = {"error": str(e)}

    try_step("links", links.normalize)
    try_step("index", build)
    if not a.quick:
        try_step("edges", _edges_build)
        try_step("pmi", pmi_build)
        try_step("entropy", entropy_score)
    # hub clusters rebuild nightly (clustering over the whole corpus is slow);
    # new docs join their topic hub on the next sync.

    print(json.dumps({"results": results, "errors": errors}, indent=2, default=str))
    sys.exit(1 if errors else 0)


def _edges_build() -> dict:
    import subprocess
    import sys as _sys

    from pathlib import Path

    r = subprocess.run(
        [_sys.executable, "-m", "kb.edges", "build"],
        capture_output=True, text=True, timeout=3600,
        cwd=Path(__file__).parent.parent,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-400:])
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "", flush=True)
    return {"ok": True}


if __name__ == "__main__":
    main()
