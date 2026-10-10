"""Generic llms.txt docs source (any site publishing llms.txt with .md page twins).

Used for Claude/Anthropic docs (platform.claude.com). Reuses the Workato docs
TOC parser; page URLs come straight from the llms.txt.

    python3 -m kb.llms_source claude   # sync the Claude docs corpus
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from .common import corpus_dir, fetch_all, fetch_cached, load_manifest, save_manifest, sha256_text
from .docs_source import parse_llms

SITES = {
    # name -> llms.txt URL (pages fetched as-is from their .md URLs)
    "claude": "https://docs.claude.com/llms.txt",
}


def sync(site: str = "claude") -> dict:
    llms_url = SITES[site]
    toc_fetch = fetch_cached(llms_url)
    if toc_fetch.status != 200:
        raise RuntimeError(f"{llms_url} fetch failed: {toc_fetch.error or toc_fetch.status}")
    entries = parse_llms(toc_fetch.text)
    # Claude llms.txt links .md URLs directly; Workato-style also ends .md
    manifest = load_manifest(site)
    out_dir = corpus_dir(site)
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = [(e.url, manifest.get(e.url, {}).get("etag", "")) for e in entries]
    results = fetch_all(jobs, progress_every=150)

    changed = unchanged = 0
    errors = []
    for entry, res in zip(entries, results):
        rec = manifest.get(entry.url, {})
        if res.status == 200:
            slug = urlparse(entry.url).path.strip("/").replace("/", "_")
            body = out_dir / f"{slug}.md"
            body.write_text(res.text)
            rec.update(
                {
                    "url": entry.url,
                    "path": f"{site}/{body.name}",
                    "etag": res.etag,
                    "sha256": sha256_text(res.text),
                    "title": entry.title,
                    "description": entry.description,
                    "section": entry.section,
                    "subsection": entry.subsection,
                }
            )
            manifest[entry.url] = rec
            changed += 1
        elif res.status == 304:
            manifest[entry.url] = rec
            unchanged += 1
        else:
            errors.append({"url": entry.url, "error": res.error})

    for e in entries:
        if e.url in manifest:
            manifest[e.url].update(
                title=e.title, description=e.description, section=e.section, subsection=e.subsection
            )
    save_manifest(site, manifest)
    return {
        "source": site,
        "toc_entries": len(entries),
        "changed": changed,
        "unchanged": unchanged,
        "removed": sorted(set(manifest) - {e.url for e in entries}),
        "errors": errors,
    }


if __name__ == "__main__":
    import sys

    site = sys.argv[1] if len(sys.argv) > 1 else "claude"
    print(json.dumps(sync(site), indent=2))
