"""docs.workato.com source: llms.txt TOC + per-page raw-Markdown sync with ETags.

llms.txt structure:
  # Workato docs
  > description
  ## Table of Contents
  ### AIRO                      <- section
  - [Title](https://.../en/airo.md): description
  #### Blueprints               <- subsection
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .common import (
    FetchResult,
    corpus_dir,
    fetch_all,
    fetch_cached,
    load_manifest,
    manifest_path,
    save_manifest,
    sha256_text,
)

LLMS_URL = "https://docs.workato.com/llms.txt"
SOURCE = "docs"

_ENTRY_RE = re.compile(r"^- \[(.+?)\]\((\S+?)\)(?:\: (.*))?$")


@dataclass
class TocEntry:
    section: str
    subsection: str
    title: str
    url: str
    description: str


def parse_llms(text: str) -> list[TocEntry]:
    entries: list[TocEntry] = []
    section = ""
    subsection = ""
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("### "):
            section = line[4:].strip()
            subsection = ""
            continue
        if line.startswith("#### "):
            subsection = line[5:].strip()
            continue
        m = _ENTRY_RE.match(line)
        if m:
            title, url, desc = m.group(1), m.group(2), m.group(3) or ""
            entries.append(
                TocEntry(
                    section=section,
                    subsection=subsection,
                    title=title,
                    url=url,
                    description=desc,
                )
            )
    return entries


def local_path_for(url: str) -> str:
    """https://docs.workato.com/en/airo/chat/chat-history.md -> docs/en/airo/chat/chat-history.md"""
    assert url.startswith("https://docs.workato.com/"), url
    rel = url[len("https://docs.workato.com/") :]
    rel = rel.split("#")[0].split("?")[0]
    if not rel.endswith(".md"):
        rel += ".md"
    return f"docs/{rel}"


def sync() -> dict:
    """Full TOC sync + conditional page fetch. Returns a summary dict."""
    toc_fetch = fetch_cached(LLMS_URL)
    if toc_fetch.status != 200:
        raise RuntimeError(f"llms.txt fetch failed: {toc_fetch.error or toc_fetch.status}")
    toc_text = toc_fetch.text
    entries = parse_llms(toc_text)
    toc_file = corpus_dir(SOURCE) / "_toc.txt"
    toc_file.parent.mkdir(parents=True, exist_ok=True)
    toc_file.write_text(toc_text)

    manifest = load_manifest(SOURCE)
    jobs = [(e.url, manifest.get(e.url, {}).get("etag", "")) for e in entries]
    results = fetch_all(jobs)

    changed, unchanged, errors = 0, 0, []
    for entry, res in zip(entries, results):
        rec = manifest.get(entry.url, {})
        if res.status == 200:
            path = local_path_for(entry.url)
            body = corpus_dir(SOURCE) / path.removeprefix("docs/")
            body.parent.mkdir(parents=True, exist_ok=True)
            body.write_text(res.text)
            rec.update(
                {
                    "url": entry.url,
                    "path": path,
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
            rec.setdefault("url", entry.url)
            manifest[entry.url] = rec
            unchanged += 1
        else:
            errors.append({"url": entry.url, "error": res.error})

    # TOC metadata updates even for unchanged pages (titles/descriptions drift)
    for e in entries:
        if e.url in manifest:
            manifest[e.url].update(
                title=e.title,
                description=e.description,
                section=e.section,
                subsection=e.subsection,
            )

    save_manifest(SOURCE, manifest)

    removed = sorted(set(manifest) - {e.url for e in entries})
    summary = {
        "source": SOURCE,
        "toc_entries": len(entries),
        "changed": changed,
        "unchanged": unchanged,
        "removed": removed,
        "errors": errors,
    }
    return summary


if __name__ == "__main__":
    import sys

    print(json.dumps(sync(), indent=2))
    sys.exit(0)
