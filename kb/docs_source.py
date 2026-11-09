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
from .wp_source import html_to_markdown

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
    # llms.txt sites disagree on heading levels: Workato/Claude use `###` for
    # sections, Temporal-style indexes use `##`. When a file has no `###`
    # headings at all, `##` is the section level (additive — never fires for
    # the Workato/Claude shape).
    sec_level = "## " if ("### " not in text and "\n## " in text) else "### "
    sub_level = "### " if sec_level == "## " else "#### "
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith(sec_level):
            section = line[len(sec_level):].strip()
            subsection = ""
            continue
        if line.startswith(sub_level):
            subsection = line[len(sub_level):].strip()
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

    # Mirror-withdrawn guard (2026-10-26 docs drop): a URL still in the
    # manifest but gone from the TOC is only a true deletion when BOTH the
    # .md mirror and the plain HTML page are gone. A live HTML page is
    # fetched and converted to markdown in place, so the doc keeps
    # refreshing instead of freezing at its last mirror copy.
    toc_urls = {e.url for e in entries}
    removed: list[str] = []
    html_fallback: list[str] = []
    mirror_revived: list[str] = []
    for url in sorted(set(manifest) - toc_urls):
        rec = manifest[url]
        path = local_path_for(url)
        body_file = corpus_dir(SOURCE) / path.removeprefix("docs/")
        md = fetch_cached(url)
        if md.status == 200:
            # mirror is serving again (TOC lag): refresh like a normal fetch
            body_file.parent.mkdir(parents=True, exist_ok=True)
            body_file.write_text(md.text)
            rec.update(
                {
                    "url": url,
                    "path": path,
                    "etag": md.etag,
                    "sha256": sha256_text(md.text),
                    "mirror": "ok",
                }
            )
            mirror_revived.append(url)
            changed += 1
            continue
        html_url = url[:-3] if url.endswith(".md") else url
        html = fetch_cached(html_url, rec.get("html_etag", ""))
        if html.status == 200:
            body_md = html_to_markdown(html.text)
            body_file.parent.mkdir(parents=True, exist_ok=True)
            body_file.write_text(body_md)
            rec.update(
                {
                    "url": url,
                    "path": path,
                    "html_etag": html.etag,
                    "sha256": sha256_text(body_md),
                    "mirror": "withdrawn",
                    "html_url": html_url,
                }
            )
            html_fallback.append(url)
            changed += 1
        elif html.status == 304:
            # mirror still withdrawn, HTML unchanged since last fallback fetch
            rec.update({"mirror": "withdrawn", "html_url": html_url})
            unchanged += 1
        else:
            # .md mirror gone AND HTML page gone: a true upstream deletion
            # (e.g. data-pipeline-recipe). Reported as removed; the corpus
            # keeps the last fetched copy (deletes stay manual).
            removed.append(url)

    save_manifest(SOURCE, manifest)

    summary = {
        "source": SOURCE,
        "toc_entries": len(entries),
        "changed": changed,
        "unchanged": unchanged,
        "removed": removed,
        "html_fallback": html_fallback,
        "mirror_revived": mirror_revived,
        "errors": errors,
    }
    return summary


if __name__ == "__main__":
    import sys

    print(json.dumps(sync(), indent=2))
    sys.exit(0)
