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
}

# system-design-primer ships translations (es/ja/zh...) and images; keep en root only.
SKIP_PARTS = {"solutions", "translations", "images", "assets", ".github"}


def _update_repo(name: str, url: str, dest: Path) -> str:
    if dest.exists() and (dest / ".git").exists():
        subprocess.run(["git", "-C", str(dest), "pull", "--ff-only", "-q"], check=True)
        return "updated"
    subprocess.run(
        ["git", "clone", "--depth", "1", "-q", url, str(dest)],
        check=True,
    )
    return "cloned"


def _iter_markdown(repo_dir: Path) -> list[Path]:
    out = []
    for p in sorted(repo_dir.rglob("*.md")):
        rel = p.relative_to(repo_dir)
        if any(part in SKIP_PARTS for part in rel.parts[:-1]):
            continue
        out.append(p)
    return out


def sync() -> dict:
    base = corpus_dir("github")
    base.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest("github")
    summary = {}
    for name, url in REPOS.items():
        dest = base / name
        try:
            action = _update_repo(name, url, dest)
        except subprocess.CalledProcessError as e:
            summary[name] = {"error": f"git failed: {e.returncode}"}
            continue
        changed = unchanged = 0
        seen = set()
        for f in _iter_markdown(dest):
            rel_path = f.relative_to(corpus_dir(""))
            blob_url = f"{url[:-4]}/blob/HEAD/{f.relative_to(dest)}"
            digest = sha256_text(f.read_text())
            seen.add(blob_url)
            rec = manifest.get(blob_url, {})
            if rec.get("sha256") == digest:
                unchanged += 1
                continue
            title = f.name.removesuffix(".md").replace("-", " ").replace("_", " ")
            first = next((ln for ln in f.read_text().splitlines() if ln.strip().startswith("#")), "")
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
