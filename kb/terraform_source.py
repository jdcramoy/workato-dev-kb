"""Terraform Registry catalog source: METADATA ONLY, cite-by-link docs.

Standing constraint (wave-1 gotcha): HashiCorp docs EXPLICITLY FORBID bulk
ingest of provider DOCUMENTATION pages — those are cite-by-link only. What we
ingest is registry-listing METADATA (namespace, name, description, tier,
downloads) from the public registry API.

Endpoint notes (probed 2026-10-15):
- /v1/providers is unusable for a full walk: its offset silently clamps at 100
  (any offset>100 answers current_offset=100), so it exposes ~200 rows.
- /v2/providers (JSON:API) honors page[number]/page[size]; page[size]=100 is
  accepted (meta reports the applied size). ~7.5k providers over ~75 pages.

Cache: the full walk lands in corpus/terraform-registry/_raw/providers.json.
Nightly sync is cheap: fetch page 1, compare its sha to the stored probe; only
when page 1 changed do we re-walk every page.

    ~/Documents/workato-kb/.venv/bin/python -m kb.terraform_source
"""

from __future__ import annotations

import json

from .common import corpus_dir, fetch_all, fetch_cached, load_manifest, save_manifest, sha256_text

SOURCE = "terraform-registry"
V2 = "https://registry.terraform.io/v2/providers"
PAGE_SIZE = 100
DOCS_URL = "https://registry.terraform.io/providers/{ns}/{name}/latest"

RAW = corpus_dir(SOURCE) / "_raw" / "providers.json"
PROBE = corpus_dir(SOURCE) / "_raw" / "page1.sha"


def _page_url(page: int) -> str:
    return f"{V2}?page[number]={page}&page[size]={PAGE_SIZE}"


def _page(page: int) -> dict:
    res = fetch_cached(_page_url(page))
    if res.status != 200:
        raise RuntimeError(f"terraform registry page {page} fetch failed: {res.error}")
    return json.loads(res.text)


def _provider_rows() -> tuple[list[dict], str]:
    """Full provider list + page-1 sha probe (cheap nightly gate)."""
    first = _page(1)
    probe_sha = sha256_text(json.dumps(first, sort_keys=True))
    if RAW.exists() and PROBE.read_text().strip() == probe_sha:
        return json.loads(RAW.read_text()), probe_sha

    total_pages = first["meta"]["pagination"]["total-pages"]
    jobs = [(_page_url(p), "") for p in range(2, total_pages + 1)]
    rest = fetch_all(jobs, progress_every=25)
    rows = [p["attributes"] for p in first["data"]]
    for r in rest:
        if r.status != 200:
            raise RuntimeError(f"terraform registry walk failed: {r.url} {r.error}")
        rows += [item["attributes"] for item in json.loads(r.text)["data"]]

    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(json.dumps(rows))
    PROBE.write_text(probe_sha)
    return rows, probe_sha


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def sync() -> dict:
    rows, probe_sha = _provider_rows()

    # v2 rows are provider-level (no version field); dedupe defensively to the
    # latest-published row per namespace/name (rows carry full-name uniquely;
    # keep the higher-download duplicate should the registry ever emit one)
    latest: dict[str, dict] = {}
    for r in rows:
        key = f"{r['namespace']}/{r['name']}"
        cur = latest.get(key)
        if cur is None or (r.get("downloads") or 0) > (cur.get("downloads") or 0):
            latest[key] = r

    by_ns: dict[str, list[dict]] = {}
    for r in latest.values():
        by_ns.setdefault(r["namespace"], []).append(r)

    manifest = load_manifest(SOURCE)
    out_dir = corpus_dir(SOURCE)
    out_dir.mkdir(parents=True, exist_ok=True)

    changed = unchanged = 0
    seen: set[str] = set()
    used_ci: set[str] = set()  # macOS FS is case-insensitive: guard case variants
    for ns in sorted(by_ns):
        providers = sorted(by_ns[ns], key=lambda r: r["name"].lower())
        fname = f"{_safe(ns)}.md"
        n = 0
        while fname.lower() in used_ci:
            n += 1
            fname = f"{_safe(ns)}-{n}.md"
        used_ci.add(fname.lower())
        lines = [
            f"# Terraform providers: {ns}",
            "",
            f"{len(providers)} providers published under the `{ns}` namespace on the "
            "Terraform Registry. Documentation is cite-by-link only (HashiCorp "
            "forbids bulk doc ingest), so each row links to the provider's registry "
            "page.",
            "",
            "| name | tier | downloads | description | docs url |",
            "| --- | --- | --- | --- | --- |",
        ]
        for r in providers:
            lines.append(
                f"| {_cell(r['name'])} | {_cell(r.get('tier'))} "
                f"| {r.get('downloads') or 0} | {_cell(r.get('description'))} "
                f"| {DOCS_URL.format(ns=ns, name=r['name'])} |"
            )
        lines.append("")
        md = "\n".join(lines)

        url = f"{SOURCE}://{ns}"
        seen.add(url)
        digest = sha256_text(md)
        rec = manifest.get(url, {})
        if rec.get("sha256") == digest and rec.get("path") == f"{SOURCE}/{fname}":
            unchanged += 1
            continue
        (out_dir / fname).write_text(md)
        manifest[url] = {
            "url": url,
            "path": f"{SOURCE}/{fname}",
            "sha256": digest,
            "title": f"Terraform providers: {ns}",
            "source": SOURCE,
        }
        changed += 1

    # prune namespaces that disappeared
    stale = [u for u in manifest if u not in seen]
    for u in stale:
        rel = manifest[u].get("path", "").split("/", 1)[-1]
        if rel:
            (out_dir / rel).unlink(missing_ok=True)
        del manifest[u]

    save_manifest(SOURCE, manifest)
    return {
        "source": SOURCE,
        "providers": len(latest),
        "namespaces": len(by_ns),
        "pages_probe_sha": probe_sha[:12],
        "changed": changed,
        "unchanged": unchanged,
        "pruned": len(stale),
    }


def _safe(ns: str) -> str:
    return ns.replace("/", "_")


if __name__ == "__main__":
    print(json.dumps(sync(), indent=2))
