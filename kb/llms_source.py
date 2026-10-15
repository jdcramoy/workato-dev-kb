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
    # Scout-expanded 2026-10-10 (Josh KB-corpus expansion): workflow/orchestration
    # + API-canon docs that publish llms.txt with .md page twins.
    "temporal": "https://docs.temporal.io/llms.txt",   # links HTML paths; .md appended below
    "n8n": "https://docs.n8n.io/llms.txt",
    "prefect": "https://docs.prefect.io/llms.txt",
    "restate": "https://docs.restate.dev/llms.txt",
    "mcp": "https://modelcontextprotocol.io/llms.txt",
    "snowflake": "https://docs.snowflake.com/llms.txt",  # top index only; section llms.txt walked below
    "dbt": "https://docs.getdbt.com/llms.txt",
    "trigger": "https://trigger.dev/llms.txt",  # HTML paths; .md appended below
    # Slack developer docs (2026-10-14, Josh: we build Slack apps/bots constantly).
    # llms.txt is the curated index; SITEMAPS below pulls the full page list.
    "slack": "https://docs.slack.dev/llms.txt",
    # Hugging Face library docs (wave 5, 2026-10-15, Josh: "mine HF"): every
    # HF doc publishes llms.txt with .md page twins (one per library). Entries
    # carry a version segment; _rewrite_hf strips it for stable URLs so a new
    # release updates pages in place instead of duplicating the corpus.
    "hf-datasets": "https://huggingface.co/docs/datasets/llms.txt",
    "hf-hub": "https://huggingface.co/docs/hub/llms.txt",
    "hf-transformers": "https://huggingface.co/docs/transformers/llms.txt",
    "hf-diffusers": "https://huggingface.co/docs/diffusers/llms.txt",
    "hf-peft": "https://huggingface.co/docs/peft/llms.txt",
    "hf-accelerate": "https://huggingface.co/docs/accelerate/llms.txt",
}

# Some llms.txt indexes are CURATED (top-level pages only) but the site also
# publishes a complete machine-readable sitemap of every .md page. Merge those
# in: every page indexed, section derived from the URL path, title backfilled
# from the page's first `# ` heading at fetch time.
SITEMAPS = {
    "slack": "https://docs.slack.dev/llms-sitemap.md",  # 3,359 pages incl. API reference
}


import re

_LOCALE_SEG = re.compile(r"/(ja-jp|ko-kr|de-de|es-es|fr-fr|pt-br|zh-cn|zh-tw|it-it)/", re.I)


def _sitemap_entries(sitemap_text: str) -> list:
    from .docs_source import TocEntry

    entries = []
    seen = set()
    for line in sitemap_text.splitlines():
        line = line.strip()
        if not line.startswith("- ") or not line[2:].startswith("http"):
            continue
        url = line[2:].split()[0]
        if _LOCALE_SEG.search(url):
            continue  # localized twins (slack ships ja-jp): en only, as with Workato docs
        if url in seen:
            continue
        seen.add(url)
        path = urlparse(url).path.strip("/")
        parts = [p for p in path.split("/") if p and p != "index"]
        entries.append(
            TocEntry(
                section=parts[0] if len(parts) > 1 else "",  # root pages have no section
                subsection="",
                title="",  # backfilled from the page's first `# ` heading
                url=url,
                description="",
            )
        )
    return entries


def _title_from_body(text: str) -> str:
    for line in text.splitlines()[:30]:
        if line.startswith("# "):
            return line[2:].strip()
    return ""

# Per-site TOC-URL rewrite: the fetch URL (etag-keyed) and manifest url are the
# canonical .md page; some sites index the HTML path instead.
def _rewrite_temporal(url: str) -> str:
    return url if url.endswith(".md") else f"{url}.md"


def _rewrite_hf(url: str) -> str:
    # huggingface.co/docs/datasets/v4.8.4/use_with_pyarrow.md ->
    # huggingface.co/docs/datasets/use_with_pyarrow.md (versionless twin is
    # always the latest release; verified 200 text/markdown 2026-10-15)
    return _HF_VERSION_RE.sub(r"/docs/\1/", url)


_HF_VERSION_RE = re.compile(r"/docs/([a-z0-9-]+)/v[\w.-]+/")


REWRITES = {
    "temporal": _rewrite_temporal,
    "trigger": _rewrite_temporal,
    "slack": _rewrite_temporal,  # one curated llms.txt entry is a bare HTML path
    **{name: _rewrite_hf for name in SITES if name.startswith("hf-")},
}


def sync(site: str = "claude") -> dict:
    llms_url = SITES[site]
    toc_fetch = fetch_cached(llms_url)
    if toc_fetch.status != 200:
        raise RuntimeError(f"{llms_url} fetch failed: {toc_fetch.error or toc_fetch.status}")
    entries = parse_llms(toc_fetch.text)
    # Snowflake-style top indexes link NESTED section llms.txt files (27 of
    # them, annotated "(717 pages)" etc.): walk those (depth 1) and merge
    # their page entries, keeping the section label from the parent link.
    for e in list(entries):
        if e.url.rstrip("/").endswith("llms.txt"):
            sec = fetch_cached(e.url)
            if sec.status != 200:
                continue
            sub = parse_llms(sec.text)
            sec_dir = e.url.rsplit("/", 1)[0] + "/"  # resolve relative page URLs against the section file's dir
            for s in sub:
                u = s.url if s.url.startswith("http") else sec_dir + s.url
                d = dict(s.__dict__)
                d.update(url=u, section=e.title or e.description or s.section, subsection="")
                entries.append(type(e)(**d))
    entries = [e for e in entries if not e.url.rstrip("/").endswith("llms.txt")]
    rewrite = REWRITES.get(site)
    if rewrite:
        # BEFORE the sitemap merge: a curated HTML-path entry rewritten to its
        # .md twin then exactly matches the sitemap URL and dedupes there.
        entries = [
            type(e)(**{**e.__dict__, "url": rewrite(e.url)}) for e in entries
        ]
    # Full-coverage sitemap merge (see SITEMAPS): llms.txt-curated entries keep
    # their titles/sections; sitemap-only pages get path-derived sections and
    # fetch-time title backfill.
    if site in SITEMAPS:
        sm_fetch = fetch_cached(SITEMAPS[site])
        if sm_fetch.status == 200:
            known = {e.url for e in entries}
            entries.extend(e for e in _sitemap_entries(sm_fetch.text) if e.url not in known)
        else:
            print(
                f"[llms_source:{site}] sitemap fetch failed: {sm_fetch.error or sm_fetch.status}",
                flush=True,
            )
    # Some llms.txt indexes emit site-relative page URLs (/v3/api-ref/...);
    # absolutize against the llms.txt origin or fetch() explodes.
    origin = "/".join(llms_url.split("/")[:3])
    entries = [
        type(e)(**{**e.__dict__, "url": f"{origin}{e.url}" if e.url.startswith("/") else e.url})
        for e in entries
    ]
    if site in SITEMAPS:
        # llms.txt also links llms-full*.txt single-file dumps (whole-site
        # markdown) — ingesting those duplicates every page and bloats chunks.
        # Sitemap sites are page-canonical: keep .md pages only.
        dropped = [e.url for e in entries if not e.url.endswith(".md")]
        if dropped:
            print(f"[llms_source:{site}] dropping {len(dropped)} non-.md entries: {dropped[:4]}", flush=True)
            entries = [e for e in entries if e.url.endswith(".md")]
    # Claude llms.txt links .md URLs directly; Workato-style also ends .md
    manifest = load_manifest(site)
    out_dir = corpus_dir(site)
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = [(e.url, manifest.get(e.url, {}).get("etag", "")) for e in entries]
    results = fetch_all(jobs, progress_every=150)

    changed = unchanged = 0
    errors = []
    slug_owner: dict[str, str] = {}  # slug -> first URL that claimed it
    for entry, res in zip(entries, results):
        rec = manifest.get(entry.url, {})
        if res.status == 200:
            # sitemap-only entries carry no TOC title: backfill from the page's
            # first `# ` heading (falls back to the slug)
            title = entry.title or _title_from_body(res.text) or urlparse(entry.url).path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")
            section = entry.section or (urlparse(entry.url).path.strip("/").split("/")[0] if "/" in urlparse(entry.url).path.strip("/") else "")
            slug = urlparse(entry.url).path.strip("/").replace("/", "_")
            slug = slug[:-3] if slug.endswith(".md") else slug
            # distinct pages can collapse to one slug (slack: rtm/v2.md vs
            # rtm/rtm_v2.md) — suffix a short URL hash, deterministic so both
            # corpus paths stay stable across syncs
            if slug_owner.setdefault(slug, entry.url) != entry.url:
                slug = f"{slug}-{sha256_text(entry.url)[:8]}"
                slug_owner[slug] = entry.url
            body = out_dir / f"{slug}.md"
            body.write_text(res.text)
            rec.update(
                {
                    "url": entry.url,
                    "path": f"{site}/{body.name}",
                    "etag": res.etag,
                    "sha256": sha256_text(res.text),
                    "title": title,
                    "description": entry.description,
                    "section": section,
                    "subsection": entry.subsection,
                }
            )
            manifest[entry.url] = rec
            changed += 1
        elif res.status == 304:
            # a 304 for a URL with no stored record would persist a pathless
            # row (the corrupt-row class that killed temporal+links once) —
            # record it as an error so the next sync refetches instead
            if rec.get("path"):
                manifest[entry.url] = rec
                unchanged += 1
            else:
                errors.append({"url": entry.url, "error": "304 without stored record"})
        else:
            errors.append({"url": entry.url, "error": res.error})

    for e in entries:
        if e.url in manifest and e.title:  # sitemap-only entries: keep the backfilled title
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
