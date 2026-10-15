"""Papers with Code source: datasets, methods, SOTA evaluation tables (wave 5,
Josh 2026-10-15).

Ingests the pwc-archive HuggingFace dumps (parquet, regenerated daily,
CC-BY-SA-4.0; the github repo paperswithcode/paperswithcode-data is only a
README pointing here). One markdown doc per dataset/method; evaluation-tables
rows are grouped into one leaderboard doc per benchmark (task x dataset), a
markdown table of metric | score | model | paper in PwC's own leaderboard
order (best first).

Parquet downloads cache under corpus/pwc/_raw/ and are skipped when the local
file matches the remote size — evaluation-tables is ~138MB across 4 shards.
Doc-level sha256 skip keeps the nightly cheap (15k+ docs).

    python3 -m kb.pwc_source
"""

from __future__ import annotations

import json
import re
import shutil
import urllib.request
from pathlib import Path

from .common import TIMEOUT, UA, corpus_dir, load_manifest, save_manifest, sha256_text

HF_BASE = "https://huggingface.co/datasets/pwc-archive"
HF_API = "https://huggingface.co/api/datasets/pwc-archive"
# known shard counts; used only when the HF API tree listing is unreachable
DECLARED_SHARDS = {"datasets": 1, "methods": 1, "evaluation-tables": 4}
SOTA_CAP = 8000  # max benchmark docs from evaluation-tables (largest kept)
CATALOG = (("datasets", "dataset"), ("methods", "method"))
# methods dump carries call-center spam entries ("how to call <brand>…" with
# non-ascii ornaments, ~8% of rows 2026-10-15). Counted, not dropped — Josh
# decides on a filter; ingesting faithfully per the wave-5 spec.
_SPAM_NAME = re.compile(r"[^\x00-\x7f].*(call|contact|support|customer|phone|number)", re.I)


def _shard_urls(name: str) -> list[str]:
    declared = DECLARED_SHARDS[name]
    fallback = [
        f"{HF_BASE}/{name}/resolve/main/data/train-{i:05d}-of-{declared:05d}.parquet"
        for i in range(declared)
    ]
    try:
        with urllib.request.urlopen(f"{HF_API}/{name}/tree/main/data", timeout=TIMEOUT) as resp:
            files = sorted(f["path"] for f in json.load(resp) if f["path"].endswith(".parquet"))
        if files:
            return [f"{HF_BASE}/{name}/resolve/main/{p}" for p in files]
    except Exception:  # noqa: BLE001 - API hiccup falls back to declared sharding
        pass
    return fallback


def _get_parquet(url: str, dest: Path) -> str:
    """Download unless dest exists with the remote size (HEAD check)."""
    remote = 0
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            remote = int(resp.headers.get("Content-Length") or 0)
    except Exception:  # noqa: BLE001 - unknown size just means no skip check
        pass
    if dest.exists() and (not remote or dest.stat().st_size == remote):
        return "cached"
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)
    return "downloaded"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:100] or "unnamed"


def _unique(slug: str, used: set) -> str:
    base, n = slug, 2
    while slug in used:
        slug = f"{base}-{n}"
        n += 1
    used.add(slug)
    return slug


def _page_slug(row: dict) -> str:
    u = (row.get("url") or "").rstrip("/")
    if u:
        tail = u.rsplit("/", 1)[-1]
        if tail:
            return tail[:100]
    return _slug(row.get("name") or "")


def _cell(text: object) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _link(text: str, url: str) -> str:
    text = _cell(text) or url
    return f"[{text}]({_cell(url)})" if url else text


def _emit_catalog(rows: list[dict], cat: str, label: str, manifest: dict,
                  used: set, seen: set, stats: dict) -> tuple[int, int, int]:
    """One doc per dataset/method row. Returns (changed, skipped, spam_likely)."""
    changed = skipped = spam = 0
    out_dir = corpus_dir("pwc") / cat
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, row in enumerate(rows):
        if i % 4000 == 0:
            print(f"pwc: {cat} {i}/{len(rows)}", flush=True)
        name = _cell(row.get("name"))
        if not name:
            continue
        if _SPAM_NAME.search(name):
            spam += 1
        desc = _cell(row.get("description"))
        page = (row.get("url") or "").strip() or f"https://paperswithcode.com/{label}/{_slug(name)}"
        slug = _unique(_page_slug(row), used)
        lines = [f"# PwC {label}: {name}", ""]
        if desc:
            lines.append(desc)
        else:
            lines.append("Catalog entry; no description in the Papers with Code dump.")
        lines.append("")
        details = []
        if cat == "datasets":
            if row.get("homepage"):
                details.append(f"- Homepage: {_link(row.get('homepage'), row['homepage'])}")
            tasks = [t.get("task") for t in (row.get("tasks") or []) if t.get("task")]
            if tasks:
                details.append(f"- Tasks: {_cell(', '.join(tasks[:5]))}")
            if row.get("num_papers"):
                details.append(f"- Papers using it: {row['num_papers']}")
            if row.get("license_name"):
                details.append(f"- License: {_cell(row['license_name'])}")
        else:
            paper = row.get("paper") or {}
            if paper.get("title"):
                details.append(f"- Introduced in: {_link(paper.get('title'), paper.get('url') or '')}")
            if row.get("introduced_year"):
                details.append(f"- Year: {row['introduced_year']}")
            if row.get("num_papers"):
                details.append(f"- Papers using it: {row['num_papers']}")
        lines += details
        lines += [
            "",
            f"- Papers with Code: {page}",
            f"- HuggingFace source: {HF_BASE}/{cat}",
        ]
        md = "\n".join(lines).rstrip() + "\n"
        digest = sha256_text(md)
        murl = f"pwc://{cat}/{slug}"
        rel = f"pwc/{cat}/{slug}.md"
        seen.add(murl)
        rec = manifest.get(murl, {})
        if rec.get("sha256") == digest and rec.get("path") == rel:
            skipped += 1
            continue
        (out_dir / f"{slug}.md").write_text(md)
        manifest[murl] = {
            "url": murl,
            "path": rel,
            "sha256": digest,
            "title": f"PwC {label}: {name}",
            "source": "pwc",
            "source_url": page,
        }
        changed += 1
    return changed, skipped, spam


def _sota_url(entry: dict) -> str:
    for link in entry.get("dataset_links") or []:
        u = link.get("url") or ""
        if "/sota/" in u:
            return u
    return ""


def _collect_benchmarks(shard_paths: list[Path], pq) -> dict[str, tuple[str, dict]]:
    """Walk the nested task/subtask/dataset tree; key by PwC leaderboard URL."""

    def walk(node: dict, top: str, out: dict) -> None:
        task = node.get("task") or top
        for d in (node.get("datasets") or []) + (node.get("subdatasets") or []):
            key = _sota_url(d) or f"{_slug(task)}--{_slug(d.get('dataset') or '')}"
            out.setdefault(key, (task, d))
        for st in node.get("subtasks") or []:
            walk(st, task, out)

    out: dict[str, tuple[str, dict]] = {}
    for p in shard_paths:
        print(f"pwc: grouping evaluation-tables {p.name}", flush=True)
        for row in pq.read_table(p).to_pylist():
            walk(row, row.get("task") or "", out)
    return out


def _sota_doc(task: str, d: dict) -> str:
    sota = d.get("sota") or {}
    cols = [m for m in (sota.get("metrics") or []) if m]
    ds = _cell(d.get("dataset"))
    url = _sota_url(d)
    lines = [f"# PwC SOTA: {task} on {ds}", ""]
    desc = _cell(d.get("description"))
    if desc:
        lines += [desc, ""]
    lines += [
        f"- Leaderboard: {url or 'https://paperswithcode.com/sota'}",
        f"- HuggingFace source: {HF_BASE}/evaluation-tables",
        "",
        "Rows in Papers with Code leaderboard order (best first).",
        "",
        "| metric | score | model | paper |",
        "|---|---|---|---|",
    ]
    for row in sota.get("rows") or []:
        model = _cell(row.get("model_name")) or "-"
        paper_cell = _link(row.get("paper_title"), row.get("paper_url") or "") or "-"
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        nonnull = {k: v for k, v in metrics.items() if v not in (None, "")}
        picked = {k: nonnull[k] for k in cols if k in nonnull} or nonnull
        if not picked:
            lines.append(f"| - | - | {model} | {paper_cell} |")
            continue
        order = sorted(picked, key=lambda m: cols.index(m) if m in cols else len(cols))
        for m in order:
            lines.append(f"| {_cell(m)} | {_cell(picked[m])} | {model} | {paper_cell} |")
    if not (sota.get("rows") or []):
        lines.append("")
        lines.append("_No SOTA rows in the dump._")
    return "\n".join(lines).rstrip() + "\n"


def _emit_sota(groups: dict[str, tuple[str, dict]], manifest: dict,
               seen: set, stats: dict) -> tuple[int, int, int]:
    """Doc per benchmark, largest-rows-first up to SOTA_CAP. Returns (changed, skipped, n_docs)."""
    rowcounts = {k: len((d.get("sota") or {}).get("rows") or []) for k, (_, d) in groups.items()}
    keys = sorted(groups, key=lambda k: (-rowcounts[k], k))
    dropped = len(keys) - SOTA_CAP if len(keys) > SOTA_CAP else 0
    if dropped:
        stats["sota_cap"] = {"cap": SOTA_CAP, "kept": SOTA_CAP, "dropped": dropped,
                             "dropped_benchmarks": f"{dropped} smallest benchmarks (fewest SOTA rows)"}
    changed = skipped = 0
    out_dir = corpus_dir("pwc") / "sota"
    out_dir.mkdir(parents=True, exist_ok=True)
    used: set = set()
    for i, key in enumerate(keys[:SOTA_CAP]):
        if i % 2000 == 0:
            print(f"pwc: sota {i}/{min(len(keys), SOTA_CAP)}", flush=True)
        task, d = groups[key]
        md = _sota_doc(task, d)
        tail = key.rsplit("/", 1)[-1] if "/" in key else key
        slug = _unique(_slug(tail), used)
        digest = sha256_text(md)
        murl = f"pwc://sota/{slug}"
        rel = f"pwc/sota/{slug}.md"
        seen.add(murl)
        rec = manifest.get(murl, {})
        if rec.get("sha256") == digest and rec.get("path") == rel:
            skipped += 1
            continue
        (out_dir / f"{slug}.md").write_text(md)
        manifest[murl] = {
            "url": murl,
            "path": rel,
            "sha256": digest,
            "title": f"PwC SOTA: {task} on {_cell(d.get('dataset'))}",
            "source": "pwc",
            "source_url": _sota_url(d),
        }
        changed += 1
    return changed, skipped, min(len(keys), SOTA_CAP)


def sync() -> dict:
    raw = corpus_dir("pwc") / "_raw"
    raw.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest("pwc")
    seen: set = set()
    stats: dict = {"downloads": {}}

    import pyarrow.parquet as pq

    changed = skipped = 0
    for cat, label in CATALOG:
        for shard in _shard_urls(cat):
            dest = raw / cat / shard.rsplit("/", 1)[-1]
            stats["downloads"][f"{cat}/{dest.name}"] = _get_parquet(shard, dest)
        rows = []
        for p in sorted((raw / cat).glob("*.parquet")):
            rows += pq.read_table(p).to_pylist()
        used: set = set()
        c, s, spam = _emit_catalog(rows, cat, label, manifest, used, seen, stats)
        changed += c
        skipped += s
        stats[cat] = {"rows": len(rows), "changed": c, "skipped": s}
        if cat == "methods":
            stats["spam_likely_methods"] = spam
    c, s, n_sota = _emit_sota(
        _collect_benchmarks(sorted((raw / "evaluation-tables").glob("*.parquet")), pq),
        manifest, seen, stats)
    changed += c
    skipped += s
    removed = [u for u in manifest if u not in seen]
    for u in removed:
        del manifest[u]
    stats["removed_stale"] = len(removed)
    save_manifest("pwc", manifest)
    # docs present per category, straight from the saved manifest
    docs = {"datasets": 0, "methods": 0, "sota": 0}
    for url in manifest:
        m = re.match(r"pwc://(\w+)/", url)
        if m and m.group(1) in docs:
            docs[m.group(1)] += 1
    return {"changed": changed, "skipped": skipped, "docs": docs, **stats}


if __name__ == "__main__":
    print(json.dumps(sync(), indent=2, default=str))
