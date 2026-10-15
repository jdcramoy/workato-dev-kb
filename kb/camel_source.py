"""Apache Camel docs source: EIP-as-DSL + component docs (scout wave 3, Josh
2026-10-11). AsciiDoc -> Markdown via Pydowndoc; the templated option-table
include:: lines (antora build-time macros, unresolvable at rest per the
2026-10-11 scout) are dropped before conversion — all prose/examples are
self-contained.

Sparse clone (blob:none + sparse-checkout docs/core/components) keeps the repo
~small; the generated catalog copies are never checked out.

    python3 -m kb.camel_source
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .common import corpus_dir, load_manifest, save_manifest, sha256_text

# pydowndoc resolves the downdoc executable via PATH; the venv bin (where the
# Pydowndoc[bin] wheel drops it) is only on PATH when the venv is activated —
# launchd/nightly runs don't activate it.
_VENV_BIN = Path(__file__).resolve().parent.parent / ".venv" / "bin"
if _VENV_BIN.is_dir() and str(_VENV_BIN) not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = f"{_VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}"

REPO = "https://github.com/apache/camel.git"
DEST = corpus_dir("camel") / "_repo"

# what to ingest (relative globs under the repo) and what to drop
INCLUDE_DIRS = (
    "components/*/src/main/docs",
    "core/camel-core-engine/src/main/docs/modules/eips/pages",
    "core/camel-core-languages/src/main/docs/modules/languages/pages",
    "docs/user-manual/modules/ROOT/pages",
)
SKIP_PARTS = {"partials", "faq", "examples"}
SKIP_FILE = re.compile(r"(upgrade|migration|jbang-commands)")

# templated include:: lines (build-time macros) + attribute lines: dropped
_INCLUDE_TEMPLATED = re.compile(
    r"^include::\S*(component-(configure|endpoint)-(options|headers)|eip-options|eip-exchangeProperties)\S*\[\]\s*$",
    re.M,
)
_ATTRIBUTE = re.compile(r"^:[\w-]+:.*$", re.M)
_XREF = re.compile(r"xref:([^\[\]]+)\[([^\]]*)\]")


def _preprocess(adoc: str) -> str:
    out = _INCLUDE_TEMPLATED.sub("", adoc)
    out = _ATTRIBUTE.sub("", out)
    out = _XREF.sub(r"[\2](\1)", out)  # xref:page.adoc[label] -> [label](page.adoc)
    return out


def _update_repo() -> str:
    if (DEST / ".git").exists():
        r = subprocess.run(
            ["git", "-C", str(DEST), "pull", "--ff-only", "-q"],
            capture_output=True, text=True)
        if r.returncode == 0:
            return "updated"
        # Hard-sync fallback (2026-10-14 fix). The 10-12/10-13 nightly floor
        # failures were pull --ff-only aborting on a DIRTY cache: the repo is
        # a read-only source cache, so no local state is worth keeping —
        # fetch + reset --hard instead of failing the whole sync.
        sync_cmds = (
            ["git", "-C", str(DEST), "fetch", "--depth", "1", "origin", "main"],
            ["git", "-C", str(DEST), "reset", "--hard", "FETCH_HEAD"],
            ["git", "-C", str(DEST), "clean", "-fdq"],
        )
        for cmd in sync_cmds:
            rr = subprocess.run(cmd, capture_output=True, text=True)
            if rr.returncode != 0:
                detail = (rr.stderr or rr.stdout).strip()[:300]
                raise RuntimeError(f"camel repo hard-sync failed ({cmd[3] if len(cmd) > 3 else 'git'}): {detail}")
        return "hard-synced"
    DEST.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", "-q", REPO, str(DEST)],
        check=True,
    )
    subprocess.run(["git", "-C", str(DEST), "sparse-checkout", "set", "docs", "core", "components"], check=True)
    return "cloned"


def _adoc_files() -> list[Path]:
    out = []
    seen = set()
    for d in INCLUDE_DIRS:
        for p in sorted(DEST.glob(d)):
            if p.is_dir():  # INCLUDE_DIRS name dirs (some are direct page dirs): walk them
                cand = p.rglob("*.adoc")
            elif p.suffix == ".adoc":
                cand = [p]
            else:
                continue
            for f in cand:
                if f in seen or not f.is_file():
                    continue
                seen.add(f)
                rel = f.relative_to(DEST)
                if any(part in SKIP_PARTS for part in rel.parts[:-1]) or SKIP_FILE.search(f.name):
                    continue
                out.append(f)
    return out


def sync() -> dict:
    action = _update_repo()
    manifest = load_manifest("camel")
    base = corpus_dir("camel")
    base.mkdir(parents=True, exist_ok=True)
    from pydowndoc import convert_string

    changed = unchanged = errors = 0
    err_list = []
    files = _adoc_files()
    for p in files:
        rel = p.relative_to(DEST)
        blob_url = f"{REPO[:-4]}/blob/HEAD/{rel}"
        raw = p.read_text(errors="replace")
        md = convert_string(_preprocess(raw)).strip()
        digest = sha256_text(md)
        rec = manifest.get(blob_url, {})
        if rec.get("sha256") == digest:
            unchanged += 1
            continue
        if len(md) < 300:
            errors += 1
            err_list.append({"url": blob_url, "error": "thin/failed conversion"})
            continue
        slug = str(rel).replace("/", "_").removesuffix(".adoc")
        title = re.sub(r"^[=\s]+", "", next((ln for ln in md.splitlines() if ln.strip()), slug))
        body = base / f"{slug[:110]}.md"
        body.write_text(f"# {title}\n\nSource: {blob_url}\n\n{md}\n")
        manifest[blob_url] = {
            "url": blob_url,
            "path": f"camel/{body.name}",
            "sha256": digest,
            "title": title,
            "source": "camel",
        }
        changed += 1
    save_manifest("camel", manifest)
    return {"repo": action, "files": len(files), "changed": changed, "unchanged": unchanged, "errors": errors, "error_samples": err_list[:5]}


if __name__ == "__main__":
    print(json.dumps(sync(), indent=2))
