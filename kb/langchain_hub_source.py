"""LangChain Hub public prompt catalog (Josh wave 5b, 2026-10-15: "go nuts").

The public Hub API DOES have a listing endpoint (the 2026-10-15 probe that
"found no list API" was hitting the wrong paths):
  GET https://api.hub.langchain.com/repos?limit=100&offset=N  -> {"repos": [...], "total": N}
  GET https://api.hub.langchain.com/commits/<owner>/<repo>    -> {"commits": [{manifest: ...}], "total": N}

Each repo row carries full metadata (description, readme, tags, likes/
downloads/views, num_commits, last_commit_hash); the TEMPLATE itself lives in
the commit manifest — a serialized LangChain prompt object where `template`
can sit at kwargs.template (PromptTemplate) or nest under
kwargs.messages[i].kwargs.prompt.kwargs.template (ChatPromptTemplate), so
extraction is a recursive walk, not a fixed path.

Docs: one per prompt repo — metadata + every template found in the manifest.
Manifest keyed hub://<owner>/<repo> (pseudo-URL — fractal_audit resolves those
since the wave-5 norm_src fix). sha-skip on unchanged repos.

    .venv/bin/python -m kb.langchain_hub_source            # full sync
    .venv/bin/python -m kb.langchain_hub_source --pages 2  # first N pages only
"""

from __future__ import annotations

import argparse
import json
from urllib.parse import quote

from .common import corpus_dir, fetch_all, fetch_cached, load_manifest, save_manifest, sha256_text

LIST_URL = "https://api.hub.langchain.com/repos"
PAGE_SIZE = 100
MAX_PAGES = 400  # hard stop: 10k repos / 100 = ~104 pages today
LIST_WORKERS = 8

# repo stats that mark a repo as changed (cheap diff before paying for a
# /commits fetch)
_CHANGE_FIELDS = ("updated_at", "num_commits", "last_commit_hash")


def _walk_listing(pages: int | None = None) -> tuple[list[dict], int, bool]:
    """All public repos: ([{...repo rows...}], total, complete).

    `complete` is False when ANY listing page failed — the caller MUST NOT
    prune "removed" repos then (a 429'd page is not an upstream deletion;
    the 2026-10-15 bug: failed pages pruned 5k freshly-written docs).
    """
    first = fetch_cached(f"{LIST_URL}?limit={PAGE_SIZE}&offset=0")
    if first.status != 200:
        raise RuntimeError(f"hub listing fetch failed: {first.error or first.status}")
    d = json.loads(first.text)
    repos: list[dict] = d.get("repos", [])
    total = int(d.get("total", 0))
    max_page = pages if pages else min((total + PAGE_SIZE - 1) // PAGE_SIZE, MAX_PAGES)
    jobs = [
        (f"{LIST_URL}?limit={PAGE_SIZE}&offset={off}", "")
        for off in range(PAGE_SIZE, max_page * PAGE_SIZE, PAGE_SIZE)
    ]
    page_failures: list[str] = []
    if jobs:
        for (u, _), res in zip(jobs, fetch_all(jobs, progress_every=20)):
            if res.status == 200:
                try:
                    repos.extend(json.loads(res.text).get("repos", []))
                except json.JSONDecodeError:
                    page_failures.append(u)
            else:
                page_failures.append(u)
        # one serial retry sweep for failed pages (the 429 breaker has cooled
        # by the time the first pass ends)
        still_bad = []
        for u in page_failures:
            r2 = fetch_cached(u)
            if r2.status == 200:
                try:
                    repos.extend(json.loads(r2.text).get("repos", []))
                    continue
                except json.JSONDecodeError:
                    pass
            still_bad.append(u)
        page_failures = still_bad
    complete = not page_failures
    # dedupe (upstream re-shuffles between page fetches)
    seen: dict[str, dict] = {}
    for r in repos:
        fn = r.get("full_name")
        if fn:
            seen[fn] = r
    return list(seen.values()), total, complete


def _extract_templates(manifest: dict, out: list[dict]) -> None:
    """Recursively collect every {template, template_format, input_variables}
    node from a serialized LangChain prompt manifest."""
    if isinstance(manifest, dict):
        if "template" in manifest and isinstance(manifest.get("template"), str):
            out.append(
                {
                    "template": manifest["template"],
                    "template_format": manifest.get("template_format", ""),
                    "input_variables": manifest.get("input_variables", []),
                    "class": ":".join(str(x) for x in manifest.get("id", [])),
                }
            )
        for v in manifest.values():
            _extract_templates(v, out)
    elif isinstance(manifest, list):
        for v in manifest:
            _extract_templates(v, out)


def _latest_template(full_name: str) -> dict:
    """Latest commit's extracted templates for one prompt repo."""
    r = fetch_cached(f"https://api.hub.langchain.com/commits/{quote(full_name, safe='/')}")
    if r.status != 200:
        return {"error": r.error or str(r.status)}
    try:
        commits = json.loads(r.text).get("commits", [])
    except json.JSONDecodeError as e:
        return {"error": f"bad json: {e}"}
    if not commits:
        return {"error": "no commits"}
    templates: list[dict] = []
    _extract_templates(commits[0].get("manifest", {}), templates)
    return {"templates": templates}


def _render_doc(repo: dict, detail: dict) -> str:
    full = repo.get("full_name", "unknown")
    lines = [
        f"# LangChain Hub prompt: {full}",
        "",
        f"- Hub page: https://smith.langchain.com/hub/{full}",
        f"- Description: {(repo.get('description') or '').strip() or '(none)'}",
    ]
    if repo.get("readme"):
        lines += ["", "## Readme", "", str(repo["readme"]).strip()]
    tags = repo.get("tags") or []
    if tags:
        lines += ["", f"- Tags: {', '.join(str(t) for t in tags)}"]
    lines += [
        f"- Likes: {repo.get('num_likes', 0)} | Downloads: {repo.get('num_downloads', 0)}"
        f" | Views: {repo.get('num_views', 0)} | Commits: {repo.get('num_commits', 0)}",
        f"- Created: {repo.get('created_at', '')} | Updated: {repo.get('updated_at', '')}",
        "",
    ]
    if detail.get("error"):
        lines += [f"## Template", "", f"(manifest fetch failed: {detail['error']})", ""]
    else:
        for i, t in enumerate(detail.get("templates", []), 1):
            head = f"Template {i}" if len(detail.get("templates", [])) > 1 else "Template"
            if t.get("class"):
                head += f" ({t['class']})"
            lines += [f"## {head}", ""]
            if t.get("input_variables"):
                lines += [f"Input variables: `{', '.join(t['input_variables'])}`", ""]
            if t.get("template_format"):
                lines += [f"Template format: `{t['template_format']}`", ""]
            lines += ["```text", t["template"], "```", ""]
        if not detail.get("templates"):
            lines += ["## Template", "", "(none found in latest manifest)", ""]
    return "\n".join(lines)


def sync(pages: int | None = None) -> dict:
    base = corpus_dir("langchain-hub")
    raw_dir = base / "_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    repos, total, complete = _walk_listing(pages)
    manifest = load_manifest("langchain-hub")

    changed = unchanged = templates_found = 0
    errors: list[dict] = []
    removed_keys: set[str] = set(manifest)

    # detail fetch jobs only for NEW or CHANGED repos (sha-skip the rest)
    jobs: list[tuple[str, dict]] = []
    for repo in repos:
        full = repo["full_name"]
        key = f"hub://{full}"
        removed_keys.discard(key)
        rec = manifest.get(key, {})
        sig = [str(repo.get(f)) for f in _CHANGE_FIELDS]
        if rec and rec.get("_sig") == sig and rec.get("sha256"):
            unchanged += 1
            continue
        jobs.append((f"https://api.hub.langchain.com/commits/{quote(full, safe='/')}", repo))
    if jobs:
        print(f"[langchain-hub] fetching {len(jobs)} commit manifests ...", flush=True)
        # CHUNKED: fetch+write in batches with a manifest save per chunk — a
        # killed run (the 2026-10-15 full sync ran ~18 min buffered-all-then-
        # write before being stopped) keeps everything landed so far
        CHUNK = 200
        for ci in range(0, len(jobs), CHUNK):
            chunk = jobs[ci : ci + CHUNK]
            results = fetch_all([(u, "") for u, _ in chunk], progress_every=200)
            chunk_changed = 0
            for (url, repo), res in zip(chunk, results):
                full = repo["full_name"]
                key = f"hub://{full}"
                if res.status != 200:
                    errors.append({"url": key, "error": res.error or str(res.status)})
                    continue
                try:
                    commits = json.loads(res.text).get("commits", [])
                    detail: dict = {}
                    if not commits:
                        detail = {"error": "no commits"}
                    else:
                        templates: list[dict] = []
                        _extract_templates(commits[0].get("manifest", {}), templates)
                        detail = {"templates": templates}
                except json.JSONDecodeError as e:
                    detail = {"error": f"bad json: {e}"}
                except Exception as e:  # noqa: BLE001 - one bad manifest never kills the sync
                    detail = {"error": str(e)}
                if detail.get("error") and "no commits" in str(detail.get("error")):
                    continue
                body = _render_doc(repo, detail)
                slug = full.replace("/", "__")
                out = base / f"{slug}.md"
                out.write_text(body)
                digest = sha256_text(body)
                manifest[key] = {
                    "url": key,
                    "path": f"langchain-hub/{out.name}",
                    "sha256": digest,
                    "title": f"LangChain Hub prompt: {full}",
                    "description": (repo.get("description") or "")[:300],
                    "section": "hub",
                    "tags": [str(t) for t in (repo.get("tags") or [])],
                    "num_downloads": repo.get("num_downloads", 0),
                    "num_likes": repo.get("num_likes", 0),
                    "_sig": [str(repo.get(f)) for f in _CHANGE_FIELDS],
                }
                changed += 1
                chunk_changed += 1
                templates_found += len(detail.get("templates", []))
            save_manifest("langchain-hub", manifest)
            print(
                f"[langchain-hub] chunk {ci // CHUNK + 1}/{(len(jobs) + CHUNK - 1) // CHUNK}: "
                f"{chunk_changed} docs (total changed {changed})",
                flush=True,
            )
    # prune removed repos — ONLY on a complete unscoped walk: a scoped --pages
    # run sees a subset of the listing (must never prune what it didn't look
    # at — 2026-10-15: a --pages 1 pilot pruned 5,885 manifest rows + files),
    # and a walk with failed pages must never read a 429'd page as an upstream
    # deletion (same day: failed listing pages pruned 5k fresh docs)
    if pages is not None or not complete:
        removed_keys = set()
    for key in removed_keys:
        rec = manifest.pop(key, {})
        p = rec.get("path")
        if p and (corpus_dir("") / p).exists():
            (corpus_dir("") / p).unlink(missing_ok=True)
    save_manifest("langchain-hub", manifest)
    return {
        "source": "langchain-hub",
        "repos": len(repos),
        "total_reported": total,
        "changed": changed,
        "unchanged": unchanged,
        "templates": templates_found,
        "removed": len(removed_keys),
        "errors": len(errors),
        "error_samples": errors[:5],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=None)
    args = ap.parse_args()
    print(json.dumps(sync(args.pages), indent=2))
