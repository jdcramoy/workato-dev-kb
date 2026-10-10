"""Corpus link normalization: turn website-style links into vault-resolvable ones.

Obsidian (and any md graph tool) only draws graph edges for links that resolve to
real files. The corpus is full of website-native links that never resolve:

  1. relative .html links     ](MessageChannel.html)          (~16k, mostly eip/)
  2. absolute docs md links   ](https://docs.workato.com/en/airo/chat.md)

Both are rewritten to RELATIVE .md links when (and only when) the target actually
exists in the corpus — links that resolve nowhere are left untouched so clicking
them keeps being harmless instead of lying.

Runs automatically as a sync post-processing step (see kb.sync, before index build)
or standalone:

  python3 -m kb.links --dry-run    # report only, no writes
  python3 -m kb.links              # rewrite in place
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

from .common import DATA_ROOT, load_manifest, manifest_path, save_manifest, sha256_text

CORPUS = DATA_ROOT / "corpus"

# ](target) and ](target "Title") — target must not contain spaces/parens
_MD_LINK = re.compile(r"(\]\()([^)\s]+)([^)]*\))")


def _corpus_files() -> list[Path]:
    return [p for p in CORPUS.rglob("*.md") if p.is_file()]


def _stem_index(files: list[Path]) -> dict[str, list[Path]]:
    """basename-without-extension -> every corpus file with that stem."""
    by_stem: dict[str, list[Path]] = defaultdict(list)
    for p in files:
        by_stem[p.stem].append(p)
    return by_stem


def _rel(src: Path, dst: Path) -> str:
    """Relative link target from the file containing the link to the corpus target."""
    return os.path.relpath(dst, src.parent)


def _resolve_html(target: str, by_stem: dict[str, list[Path]], src: Path) -> Path | None:
    """MessageChannel.html or /patterns/messaging/MessageChannel.html -> corpus path."""
    stem = Path(target).stem
    candidates = by_stem.get(stem, [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # prefer a candidate in (or nearest to) the source directory, then by shortest path
    def _rank(c: Path) -> tuple[int, int]:
        same_dir = 0 if c.parent == src.parent else 1
        return (same_dir, len(c.parts))

    return sorted(candidates, key=_rank)[0]


def _resolve_docs_url(target: str) -> Path | None:
    """https://docs.workato.com/en/airo/chat.md[.html] -> corpus/docs/en/airo/chat.md."""
    m = re.match(r"https?://docs\.workato\.com(/en/.+?)(?:\.md|\.html)?$", target)
    if not m:
        return None
    candidate = CORPUS / "docs" / m.group(1).lstrip("/")
    if candidate.suffix != ".md":
        candidate = candidate.with_suffix(".md")
    return candidate if candidate.is_file() else None


def _refresh_manifest_shas(changed_paths: set[Path], dry_run: bool) -> int:
    """Recompute sha256 in manifests for rewritten files (or ALL files when the
    changed set is None — one-time reconciliation).

    kb.indexer compares the DB against manifest-recorded shas (not live files), so a
    rewrite must refresh the manifest or the index never sees the new text.
    """
    refreshed = 0
    for mf in sorted(manifest_path("").parent.glob("*.jsonl")):
        source = mf.stem
        records = load_manifest(source)
        dirty = False
        for rec in records.values():
            rel = rec.get("path", "")
            if not rel:
                continue
            f = CORPUS / rel
            if f.is_file() and (changed_paths is None or f in changed_paths):
                rec["sha256"] = sha256_text(f.read_text())
                refreshed += 1
                dirty = True
        if dirty and not dry_run:
            save_manifest(source, records)
    return refreshed


def normalize(dry_run: bool = False, refresh_shas: bool = False) -> dict:
    """Rewrite resolvable website links to relative .md links across the corpus.

    With refresh_shas=True, also recompute manifest shas for EVERY manifest-listed
    file (one-time reconciliation after an out-of-band rewrite).
    """
    files = _corpus_files()
    by_stem = _stem_index(files)

    touched = 0
    rewritten = 0
    unresolved = 0
    changed: set[Path] = set()
    samples: list[str] = []

    for src in files:
        try:
            text = src.read_text(errors="replace")
        except OSError:
            continue
        out: list[str] = []

        def _sub(m: re.Match) -> str:
            nonlocal rewritten, unresolved
            target, tail = m.group(2), m.group(3)
            dst = None
            if target.endswith(".html"):
                dst = _resolve_html(target, by_stem, src)
            elif "docs.workato.com" in target:
                dst = _resolve_docs_url(target)
            if dst is None:
                unresolved += 1
                return m.group(0)
            new_target = _rel(src, dst)
            if new_target != target:
                rewritten += 1
                if len(samples) < 10:
                    samples.append(f"{src.relative_to(CORPUS)}: {target} -> {new_target}")
                return f"{m.group(1)}{new_target}{tail}"
            return m.group(0)

        new_text, n = _MD_LINK.subn(_sub, text)
        if new_text != text:
            touched += 1
            changed.add(src)
            if not dry_run:
                src.write_text(new_text)

    return {
        "files_scanned": len(files),
        "files_changed": touched,
        "links_rewritten": rewritten,
        "links_left_unresolved": unresolved,
        "manifest_shas_refreshed": _refresh_manifest_shas(
            None if refresh_shas else changed, dry_run
        ),
        "dry_run": dry_run,
        "samples": samples,
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh-shas", action="store_true",
                    help="recompute manifest shas for ALL corpus files (reconciliation)")
    a = ap.parse_args()
    import json

    print(json.dumps(normalize(dry_run=a.dry_run, refresh_shas=a.refresh_shas), indent=2))


if __name__ == "__main__":
    main()
