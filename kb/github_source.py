"""GitHub source: architecture/system-design reference repos.

Shallow clone + git pull incremental; manifest over English markdown files.
These are public repos (shareable), added for deep system/architecture thinking
alongside Workato product knowledge.

    python3 -m kb.github_source
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .common import corpus_dir, load_manifest, save_manifest, sha256_text

REPOS = {
    "system-design-primer": "https://github.com/donnemartin/system-design-primer.git",
    "awesome-software-architecture": "https://github.com/mehdihadeli/awesome-software-architecture.git",
    "awesome-system-design-resources": "https://github.com/ashishps1/awesome-system-design-resources.git",
    # Scout-expanded 2026-10-10 (Josh KB-corpus expansion): open-licensed
    # architecture/integration/API-canon repos.
    "azure-architecture-center": "https://github.com/MicrosoftDocs/architecture-center.git",  # CC-BY-4.0
    "google-aip": "https://github.com/aip-dev/google.aip.dev.git",  # CC-BY-4.0
    "hacker-laws": "https://github.com/dwmkerr/hacker-laws.git",
    "standard-webhooks": "https://github.com/standard-webhooks/standard-webhooks.git",  # Apache-2.0
    "asyncapi-spec": "https://github.com/asyncapi/spec.git",  # Apache-2.0
    "cloudevents-spec": "https://github.com/cloudevents/spec.git",  # Apache-2.0 (CNCF)
    "openai-cookbook": "https://github.com/openai/openai-cookbook.git",  # MIT
    "anthropic-cookbook": "https://github.com/anthropics/anthropic-cookbook.git",  # MIT
    # Neural-network canon (scout round 2)
    "d2l-en": "https://github.com/d2l-ai/d2l-en.git",  # CC-BY-SA-4.0 full open book
    "cs231n-notes": "https://github.com/cs231n/cs231n.github.io.git",
    "nn-zero-to-hero": "https://github.com/karpathy/nn-zero-to-hero.git",
    "build-nanogpt": "https://github.com/karpathy/build-nanogpt.git",
    "spinningup": "https://github.com/openai/spinningup.git",  # MIT, Sphinx md docs
    "duckdb-web": "https://github.com/duckdb/duckdb-web.git",  # docs tree markdown
    "noc-book-2": "https://github.com/nature-of-code/noc-book-2.git",  # CC-BY-NC book; HTML content files
    # Vendor-canon (Josh 2026-10-13): Workato's OWN developer/SDK repos — the
    # Connector SDK source + examples and the custom-connector docs. The KB had
    # architecture canon but nothing FROM Workato's own engineering.
    "workato-connector-sdk": "https://github.com/workato/workato-connector-sdk.git",  # MIT
    "custom-connector-docs": "https://github.com/workato/custom_connector_docs.git",
    # Wave 5 (2026-10-15, Josh): developer-roadmap content (364MB repo, content
    # is all under content/ — sparse blobless checkout) + the llms.txt directory
    # README (the site data itself is mined by kb/llms_scout.py, see below).
    "awesome-llms-txt": "https://github.com/SecretiveShell/Awesome-llms-txt.git",
}

# Repos too large for a plain clone: sparse blobless checkout of named dirs.
SPARSE_REPOS = {
    # NOTE: the nilbuild fork restructured the upstream layout — roadmaps live
    # in roadmaps/, not content/ (first clone landed empty of pages)
    "developer-roadmap": ("https://github.com/nilbuild/developer-roadmap.git", ["roadmaps"]),
}

# Repos whose ingestable content is .html rather than .md (html_to_markdown at
# read/index time happens upstream in the chunker's text handling; here we
# just include the files).
HTML_REPOS = {"noc-book-2"}
HTML_SKIP_PARTS = {"examples", "images", "scripts", "styles", "site"}

# system-design-primer ships translations (es/ja/zh...) and images; keep en root only.
# Scout repos: azure includes snippets, cloudevents drafts/future, artwork dirs.
SKIP_PARTS = {"solutions", "translations", "images", "assets", ".github",
              "includes", "drafts", "future", "artwork", "snippets"}


def _update_repo(name: str, url: str, dest: Path, sparse_dirs: list[str] | None = None) -> str:
    if dest.exists() and (dest / ".git").exists():
        # dirty-tree recovery: local modifications (seen 2026-10-15 on
        # duckdb-web + others — suspected in-place corpus rewrite) block a
        # ff-only pull forever. The clone is a disposable cache: reset first.
        r = subprocess.run(["git", "-C", str(dest), "pull", "--ff-only", "-q"], capture_output=True)
        if r.returncode != 0:
            subprocess.run(["git", "-C", str(dest), "checkout", "--", "."], check=True)
            subprocess.run(["git", "-C", str(dest), "pull", "--ff-only", "-q"], check=True)
            return "updated-after-reset"
        return "updated"
    if sparse_dirs:
        # blobless sparse clone: big monorepos (developer-roadmap) where the
        # ingestable content sits in a known subtree
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", "-q", url, str(dest)], check=True)
        subprocess.run(["git", "-C", str(dest), "sparse-checkout", "set", *sparse_dirs], check=True)
        return "cloned-sparse"
    subprocess.run(
        ["git", "clone", "--depth", "1", "-q", url, str(dest)],
        check=True,
    )
    return "cloned"

def _iter_markdown(repo_dir: Path, name: str = "") -> list[Path]:
    out = []
    html = name in HTML_REPOS
    for p in sorted(repo_dir.rglob("*")):
        if p.suffix not in (".md", ".html"):
            continue
        if html and p.suffix == ".md":
            continue  # html repos: ingest the .html content converted below
        rel = p.relative_to(repo_dir)
        if any(part in SKIP_PARTS for part in rel.parts[:-1]):
            continue
        if html and any(part in HTML_SKIP_PARTS for part in rel.parts[:-1]):
            continue
        out.append(p)
    return out


def sync() -> dict:
    base = corpus_dir("github")
    base.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest("github")
    summary = {}
    for n2, (u2, _dirs2) in SPARSE_REPOS.items():
        if n2 in REPOS:
            raise ValueError(f"repo {n2} in both REPOS and SPARSE_REPOS")
    # normalize to (url, sparse_dirs) tuples
    repo_items: list[tuple[str, str, list[str] | None]] = [
        (n, u, None) for n, u in REPOS.items()
    ] + [(n, u, dirs) for n, (u, dirs) in SPARSE_REPOS.items()]
    for name, url, sparse_dirs in repo_items:
        dest = base / name
        try:
            action = _update_repo(name, url, dest, sparse_dirs)
        except subprocess.CalledProcessError as e:
            summary[name] = {"error": f"git failed: {e.returncode}"}
            continue
        changed = unchanged = 0
        seen = set()
        for f in _iter_markdown(dest, name):
            rel_path = f.relative_to(corpus_dir(""))
            if name in HTML_REPOS and f.suffix == ".html":
                # convert at ingest: the corpus/index reads markdown, not tags
                from .wp_source import html_to_markdown
                md_out = f.with_suffix(".md")
                if not md_out.exists():
                    md_out.write_text(html_to_markdown(f.read_text(errors="replace")))
                rel_path = md_out.relative_to(corpus_dir(""))
                f_read = md_out
                path_suffix = md_out.name
            else:
                f_read = f
                path_suffix = f.name
            blob_url = f"{url[:-4]}/blob/HEAD/{f.relative_to(dest)}"
            digest = sha256_text(f_read.read_text())
            seen.add(blob_url)
            rec = manifest.get(blob_url, {})
            if rec.get("sha256") == digest:
                unchanged += 1
                continue
            title = path_suffix.removesuffix(".md").replace("-", " ").replace("_", " ")
            first = next((ln for ln in f_read.read_text().splitlines() if ln.strip().startswith("#")), "")
            if first.startswith("# "):
                title = first[2:].strip()
            manifest[blob_url] = {
                "url": blob_url,
                "path": str(rel_path),
                "sha256": digest,
                "title": title,
                "repo": name,
                "source": "github",
            }
            changed += 1
        removed = sorted(set(manifest) - seen) if name else []
        summary[name] = {"action": action, "changed": changed, "unchanged": unchanged, "files": len(seen)}
    save_manifest("github", manifest)
    return summary


if __name__ == "__main__":
    print(json.dumps(sync(), indent=2))
