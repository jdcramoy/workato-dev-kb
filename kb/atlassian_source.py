"""Atlassian Developer (DAC) docs source: developer.atlassian.com cloud docs.

Sitemap-driven, in the html_source idiom, but with a DAC-specific twist: every
doc page embeds its content as RAW MARKDOWN in `window.__DATA__.page.document`
(a list of {type: markdown|shortcode, content} blocks) — no HTML conversion
needed. REST reference pages instead embed the full OpenAPI swagger JSON
(`window.__DATA__.swaggerUrl`); those expand to one doc per API tag (the same
granularity as DAC's own api-group-* pages, which are all renderings of the
same spec and are not fetched individually).

Internal-RAG-only (see README licensing notes).

    python3 -m kb.atlassian_source              # all products
    python3 -m kb.atlassian_source jsm-cloud    # one product
    python3 -m kb.ingest                        # then links -> index -> edges -> pmi -> entropy
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse
import concurrent.futures as cf

from .common import corpus_dir, fetch_cached, load_manifest, save_manifest, sha256_text

# DAC's CDN sheds load: at the shared pool's 12 workers ~1/3 of jira-cloud
# pages came back status 0 (Josh 2026-10-15 sync). Lower pool + one retry.
_WORKERS = 4


def _fetch_all(jobs: list[tuple[str, str]], progress_every: int = 100) -> list:
    out: list = [None] * len(jobs)
    with cf.ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        futs = {ex.submit(fetch_cached, u, e): i for i, (u, e) in enumerate(jobs)}
        done = 0
        for fut in cf.as_completed(futs):
            r = fut.result()
            for attempt in range(3):  # DAC sheds load: quiet retries with backoff
                if r.status != 0:
                    break
                time.sleep(2 * (attempt + 1))
                r = fetch_cached(*jobs[futs[fut]])
            out[futs[fut]] = r
            done += 1
            if progress_every and done % progress_every == 0:
                print(f"  ... {done}/{len(jobs)} fetched", flush=True)
    return out

MIN_MD = 400  # nav shells / stub pages produce less; skip them

# product -> sitemap. Each is a leaf sitemap listing every page in that tree.
# "Build with AI" is a nav category, not a tree — its content lives in the
# Rovo MCP / A2A trees (Josh 2026-10-15 scope note).
SITES = {
    "jira-cloud": "https://developer.atlassian.com/cloud/jira/platform/sitemap.xml",
    "jira-software": "https://developer.atlassian.com/cloud/jira/software/sitemap.xml",
    "jsm-cloud": "https://developer.atlassian.com/cloud/jira/service-desk/sitemap.xml",
    "confluence-cloud": "https://developer.atlassian.com/cloud/confluence/sitemap.xml",
    "rovo-mcp": "https://developer.atlassian.com/cloud/rovo-mcp/sitemap.xml",
    "rovo-a2a": "https://developer.atlassian.com/cloud/rovo-a2a/sitemap.xml",
}

LOCALE_SEG = re.compile(r"/(ja-jp|ko-kr|de-de|es-es|fr-fr|pt-br|zh-cn|zh-tw|it-it)/", re.I)

# versioned REST reference root, e.g. /cloud/jira/platform/rest/v3/
_REST_SEG = re.compile(r"^(?P<base>.*/rest/)(?P<ver>v\d+)?/?$")
_API_GROUP_SEG = re.compile(r"api-group-(?P<slug>.+)$")


def _extract_dac_json(html: str) -> dict | None:
    """Parse the `window.__DATA__ = {...}` blob (brace scanner, string-aware)."""
    marker = "window.__DATA__ = "
    start = html.find(marker)
    if start < 0:
        return None
    start += len(marker)
    depth = 0
    i = start
    instr = esc = False
    while i < len(html):
        c = html[i]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start : i + 1])
                except json.JSONDecodeError:
                    return None
        i += 1
    return None


def _blocks_to_md(document: list) -> str:
    """Join a DAC document block list into one markdown string.

    markdown blocks pass through; shortcode blocks (warning/info panels)
    become blockquotes carrying their title argument.
    """
    parts: list[str] = []
    for block in document:
        if not isinstance(block, dict):
            continue
        content = (block.get("content") or "").strip()
        if not content:
            continue
        if block.get("type") == "shortcode":
            args = block.get("arguments") or []
            title = next((a.get("value", "") for a in args if a.get("name") == "title"), "")
            head = f"> **{title}**" if title else ">"
            body = "\n".join(f"> {line}" for line in content.splitlines())
            parts.append(f"{head}\n{body}")
        else:
            parts.append(content)
    return "\n\n".join(parts)


def _kebab(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "other"


def _ref_name(schema: dict) -> str:
    """Top type name of a schema: $ref component, primitive type, or ''."""
    if not isinstance(schema, dict):
        return ""
    ref = schema.get("$ref", "")
    if ref:
        return ref.rsplit("/", 1)[-1]
    t = schema.get("type", "")
    if t == "array":
        return f"array<{_ref_name(schema.get('items', {}))}>"
    return t


def _render_param(p: dict) -> str:
    name = p.get("name", "?")
    loc = p.get("in", "?")
    req = "required" if p.get("required") else "optional"
    typ = _ref_name(p.get("schema", {})) or p.get("type", "")
    desc = re.sub(r"\s+", " ", p.get("description", "")).strip()
    return f"- `{name}` ({loc}, {typ}, {req}) — {desc}"


def _render_operation(method: str, path: str, op: dict) -> str:
    out = [f"## {method.upper()} {path}", ""]
    if op.get("deprecated"):
        out += ["> **DEPRECATED**", ""]
    summary = (op.get("summary") or "").strip()
    if summary:
        out += [f"**{summary}**", ""]
    desc = (op.get("description") or "").strip()
    if desc:
        out += [desc, ""]
    narrative = op.get("x-atlassian-narrative")
    if isinstance(narrative, dict):
        for d in narrative.get("documents", []):
            body = (d.get("body") or "").strip()
            if body:
                out += [body, ""]
    scopes: list[str] = []
    for s in op.get("security") or []:
        if isinstance(s, dict):
            for ss in s.values():
                scopes.extend(ss)
    oauth = op.get("x-atlassian-oauth2") or {}
    if isinstance(oauth, dict) and isinstance(oauth.get("scopes"), dict):
        scopes.extend(oauth["scopes"].values())
    if scopes:
        out += ["**Authorization:** " + ", ".join(f"`{s}`" for s in dict.fromkeys(scopes)), ""]
    params = [p for p in op.get("parameters", []) if isinstance(p, dict)]
    if params:
        out += ["**Parameters:**"]
        out.extend(_render_param(p) for p in params)
        out.append("")
    body = op.get("requestBody")
    if isinstance(body, dict):
        for ct, media in (body.get("content") or {}).items():
            name = _ref_name((media or {}).get("schema", {})) if isinstance(media, dict) else ""
            out += [f"**Request body** ({ct}): `{name or 'untyped'}`", ""]
            break
    responses = op.get("responses") or {}
    if responses:
        out += ["**Responses:**"]
        for code, resp in sorted(responses.items()):
            if not isinstance(resp, dict):
                continue
            rdesc = re.sub(r"\s+", " ", resp.get("description", "")).strip()
            ref = ""
            for _ct, media in (resp.get("content") or {}).items():
                n = _ref_name((media or {}).get("schema", {})) if isinstance(media, dict) else ""
                if n:
                    ref = f" (`{n}`)"
                    break
            out.append(f"- `{code}` — {rdesc}{ref}")
        out.append("")
    return "\n".join(out)


def _swagger_group_docs(swagger: dict, source_url: str) -> dict[str, str]:
    """OpenAPI spec -> {tag-kebab: markdown}. One doc per tag (API group)."""
    docs: dict[str, list[str]] = {}
    for path, item in sorted((swagger.get("paths") or {}).items()):
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method not in ("get", "post", "put", "delete", "patch") or not isinstance(op, dict):
                continue
            block = _render_operation(method, path, op)
            for tag in op.get("tags") or ["other"]:
                docs.setdefault(_kebab(tag), []).append(block)

    title = (swagger.get("info") or {}).get("title", "REST reference")
    out: dict[str, str] = {}
    for tag, blocks in sorted(docs.items()):
        md = [f"# {title} — {tag} API group", "", f"Spec source: {source_url}", ""]
        md.extend(blocks)
        out[tag] = "\n".join(md)
    return out


def _rest_root_of(url: str) -> str | None:
    """REST reference root for a page URL (None if not under /rest/)."""
    path = urlparse(url).path
    if not path.endswith("/"):
        path += "/"  # site_urls are rstripped; ".../rest" must still match
    m = _REST_SEG.search(path)
    if not m:
        return None
    base = m.group("base") + (m.group("ver") or "")
    return "https://developer.atlassian.com" + base.rstrip("/") + "/"


def sync(site: str | None = None) -> dict:
    names = [site] if site else list(SITES)
    manifest = load_manifest("atlassian")
    base_dir = corpus_dir("atlassian")
    base_dir.mkdir(parents=True, exist_ok=True)

    # pass 1: sitemaps -> page URLs (scoped to the product tree, en-only)
    site_urls: dict[str, list[str]] = {}
    for name in names:
        sm_url = SITES[name]
        prefix = sm_url.rsplit("/", 1)[0] + "/"
        fr = fetch_cached(sm_url)
        if fr.status != 200:
            site_urls[name] = []
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", fr.text)
        site_urls[name] = sorted(
            u.rstrip("/")
            for u in locs
            if u.startswith(prefix) and not LOCALE_SEG.search(u)
        )

    # pass 2: fetch pages with etag caching (api-group pages excluded — the
    # swagger expansion below covers them; they are renderings of one spec)
    jobs: list[tuple[str, str, str]] = []
    for name in names:
        for u in site_urls[name]:
            if _API_GROUP_SEG.search(urlparse(u).path):
                continue
            # REST roots always refetch: a 304 hides the swaggerUrl and would
            # skip the whole reference expansion (2026-10-15 sync bug)
            etag = "" if _rest_root_of(u) else manifest.get(u, {}).get("etag", "")
            jobs.append((name, u, etag))
    results = _fetch_all([(u, e) for _, u, e in jobs], progress_every=100)
    fetched: dict[str, object] = {}
    for (_name, u, _e), fr in zip(jobs, results):
        fetched[u] = fr

    summary: dict = {}
    swagger_cache: dict[str, dict | None] = {}

    for name in names:
        changed = unchanged = thin = skipped = 0
        page_urls = site_urls[name]

        # REST roots -> swagger -> one doc per API tag
        # {root_url: {tag_kebab: markdown}}; the root fetch also registers the
        # swagger etag in the manifest so re-syncs 304.
        expanded: dict[str, dict[str, str]] = {}
        root_urls = sorted({_rest_root_of(u) for u in page_urls} - {None})
        for root_url in root_urls:
            fr = fetched.get(root_url) or fetched.get(root_url.rstrip("/"))
            if not isinstance(fr, object) or getattr(fr, "status", 0) != 200:
                continue
            data = _extract_dac_json(getattr(fr, "text", "") or "")
            swagger_url = (data or {}).get("swaggerUrl")
            if not swagger_url:
                continue
            full_swagger_url = "https://developer.atlassian.com" + swagger_url
            if full_swagger_url not in swagger_cache:
                # always fetch fresh: a 304 carries no body to expand, and the
                # doc-write loop below dedupes by content hash anyway
                sfr = fetch_cached(full_swagger_url)
                if sfr.status == 0:  # DAC sheds load; one quiet retry
                    time.sleep(2)
                    sfr = fetch_cached(full_swagger_url)
                if sfr.status == 200:
                    manifest[full_swagger_url] = {
                        "url": full_swagger_url,
                        "etag": sfr.etag,
                        "title": f"{name} swagger {swagger_url.rsplit('/', 1)[-1]}",
                    }
                    try:
                        swagger_cache[full_swagger_url] = json.loads(sfr.text)
                    except json.JSONDecodeError:
                        swagger_cache[full_swagger_url] = None
                else:
                    swagger_cache[full_swagger_url] = None
            sw = swagger_cache[full_swagger_url]
            if not sw:
                skipped += 1
                continue
            tag_docs = _swagger_group_docs(sw, full_swagger_url)
            # intro doc: API-wide description + narrative + servers
            info = sw.get("info") or {}
            intro = [f"# {info.get('title', 'REST reference')} — overview", ""]
            if info.get("description"):
                intro += [info["description"], ""]
            for d in (sw.get("x-atlassian-narrative") or {}).get("documents", []):
                body = (d.get("body") or "").strip()
                if body:
                    intro += [f"## {d.get('title', d.get('anchor', ''))}", body, ""]
            for s in sw.get("servers") or []:
                intro += [f"Base URL: `{s.get('url', '')}`", ""]
            intro += [f"Full spec (schemas): {full_swagger_url}", ""]
            tag_docs["intro"] = "\n".join(intro)
            expanded[root_url] = tag_docs

        for root_url, tag_docs in expanded.items():
            root_path = urlparse(root_url).path.strip("/")
            ref_seg = root_path.split("/rest/")[-1] or "rest"
            for tag, md in tag_docs.items():
                if len(md) < MIN_MD:
                    thin += 1
                    continue
                # reuse the real api-group page URL when one exists for this tag
                url = f"{root_url}api-group-{tag}/"
                fname = f"{name}--rest-{ref_seg.replace('/', '-')}-{tag}.md"
                f = base_dir / fname
                title = md.splitlines()[0].lstrip("# ")
                body = md.split("\n", 1)[1].lstrip("\n") if md.startswith("# ") else md
                digest = sha256_text(f"# {title}\n\nSource: {url}\n\n{body}\n")
                rec = manifest.get(url, {})
                if rec.get("sha256") == digest and f.exists():
                    unchanged += 1
                    continue
                f.write_text(f"# {title}\n\nSource: {url}\n\n{body}\n")
                manifest[url] = {
                    "url": url,
                    "path": "atlassian/" + fname,
                    "sha256": digest,
                    "title": title,
                    "site": name,
                    "section": name,
                    "source": "atlassian",
                    "kind": "rest",
                }
                changed += 1

        # guide pages (markdown blocks straight out of window.__DATA__)
        for u in page_urls:
            if _API_GROUP_SEG.search(urlparse(u).path):
                continue  # covered by swagger expansion
            fr = fetched.get(u)
            status = getattr(fr, "status", 0)
            if status == 304:
                unchanged += 1
                continue
            if status != 200:
                skipped += 1
                continue
            data = _extract_dac_json(getattr(fr, "text", "") or "")
            if data and data.get("swaggerUrl"):
                continue  # REST reference root: served by swagger expansion
            page = (data or {}).get("page") or {}
            document = page.get("document")
            if not document:
                thin += 1
                continue
            md = _blocks_to_md(document)
            if len(md) < MIN_MD:
                thin += 1
                continue
            title = (
                (page.get("metadata") or {}).get("title")
                or urlparse(u).path.rstrip("/").rsplit("/", 1)[-1]
            )
            digest = sha256_text(md)
            path_seg = urlparse(u).path.strip("/").replace("/", "_") or "index"
            fname = f"{name}--{path_seg[:110]}.md"
            f = base_dir / fname
            rec = manifest.get(u, {})
            if rec.get("sha256") == digest and f.exists():
                unchanged += 1
                continue
            f.write_text(f"# {title}\n\nSource: {u}\n\n{md}\n")
            manifest[u] = {
                "url": u,
                "path": f"atlassian/{fname}",
                "sha256": digest,
                "etag": fr.etag,
                "title": title,
                "site": name,
                "section": name,
                "source": "atlassian",
                "kind": "guide",
            }
            changed += 1

        summary[name] = {
            "pages": len(page_urls),
            "changed": changed,
            "unchanged": unchanged,
            "thin": thin,
            "skipped": skipped,
            "rest_groups": sum(len(v) for v in expanded.values()),
        }

    save_manifest("atlassian", manifest)
    return summary


if __name__ == "__main__":
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(sync(arg), indent=2))
