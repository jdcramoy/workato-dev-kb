"""Workday developer-site source (Josh 2026-10-15): developer.workday.com is
AI-agent-native — llms.txt is the curated index (1,383 doc pages, Markdown at
/doc/{id}.md), REST specs + per-service changelogs are public JSON under
/bundles/rest-directory-ui/public/static/openApiFiles/, the SOAP explorer
serves numbered catalog snapshots (soap_services.N.json), and the Graph API is
self-contained in the GraphQL schema.txt.

Corpus layout (all under corpus/workday/):
    doc/<docId>.md                    llms.txt documentation pages, verbatim
    rest/<urlServiceName>_<ver>.md    REST spec converted to endpoint markdown
    rest-changelog/<...>.md           REST per-service changelog → markdown
    soap/service/<Svc>_<ver>.md       SOAP service catalog entry (docs + ops)
    graph/schema.md                   GraphQL schema (Graph API), verbatim
    wql-alias/<datasourceAlias>.md    tenant WQL alias export, one doc per source

Manifest rows carry `kind` (doc|rest-spec|rest-changelog|soap|graph|wql-alias)
plus service metadata. Raw JSON/xlsx keep in _raw/ (no manifest rows → never
indexed). ETag-conditional everywhere via kb.common.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET  # noqa: F401  (kept for future WSDL work)

from .common import (
    FetchResult,
    corpus_dir,
    fetch_all,
    fetch_cached,
    load_manifest,
    save_manifest,
    sha256_text,
)

SOURCE = "workday"
BASE = "https://developer.workday.com"
LLMS_URL = f"{BASE}/llms.txt"
REST_CATALOG_URL = f"{BASE}/bundles/rest-directory-ui/public/static/services_oas3.json"
OPENAPI_BASE = f"{BASE}/bundles/rest-directory-ui/public/static/openApiFiles"
SOAP_BASE = f"{BASE}/bundles/soap-api-explorer"
GRAPH_SCHEMA_URL = f"{BASE}/bundles/graphql-changelog/public/static/schema/schema.txt"

# Tenant WQL alias export (Josh 2026-07-15). Copied out of Downloads at wiring
# time; Downloads is a cache, the corpus is the home.
WQL_XLSX = corpus_dir(SOURCE) / "_raw" / "WQL Alias Export 2026-07-15.xlsx"

_ENTRY_RE = re.compile(r"^- \[(.+?)\]\((\S+?)\)(?:\: (.*))?$")
_TAG_RE = re.compile(r"<[^>]+>")


def _raw_dir() -> "corpus_dir":
    d = corpus_dir(SOURCE) / "_raw"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- docs ----


def _parse_llms_docs(text: str) -> list[tuple[str, str, str]]:
    """[(title, url, description)] from the ## Documentation section."""
    out = []
    in_docs = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_docs = line.strip() == "## Documentation"
            continue
        if not in_docs:
            continue
        m = _ENTRY_RE.match(line)
        if m:
            out.append((m.group(1), m.group(2), m.group(3) or ""))
    return out


def _sync_docs(manifest: dict) -> dict:
    entries = _parse_llms_docs(fetch_cached(LLMS_URL).text)
    if not entries:
        raise RuntimeError("llms.txt parsed to 0 doc entries — site format changed?")
    jobs = [(u, manifest.get(u, {}).get("etag", "")) for _, u, _ in entries]
    results = fetch_all(jobs)
    # fetch_all returns completion-ordered results — key by res.url, never zip
    # positionally (same bug class as the rest-spec body mismatch)
    by_url = {r.url: r for r in results}
    changed, unchanged, errors = 0, 0, []
    for title, url, desc in entries:
        res = by_url.get(url, FetchResult(url=url, status=0, error="missing from results"))
        rec = manifest.get(url, {})
        if res.status == 304:
            unchanged += 1
            continue
        if res.status != 200:
            if res.error:
                errors.append(f"{url}: {res.error}")
            continue
        doc_id = url.rsplit("/", 1)[-1]
        rel = f"workday/doc/{doc_id}"
        (corpus_dir(SOURCE) / "doc" / doc_id).parent.mkdir(parents=True, exist_ok=True)
        (corpus_dir(SOURCE) / "doc" / doc_id).write_text(res.text)
        rec.update(
            {
                "url": url,
                "path": rel,
                "etag": res.etag,
                "sha256": sha256_text(res.text),
                "title": title,
                "description": desc,
                "section": "Documentation",
                "kind": "doc",
            }
        )
        manifest[url] = rec
        changed += 1
    return {"changed": changed, "unchanged": unchanged, "errors": errors[:10], "total": len(entries)}


# ---------------------------------------------------------------- REST ----


def _schema_brief(schema: dict, components: dict, depth: int = 0) -> str:
    """One-line-ish schema summary: type, properties (name: type), $ref resolved once."""
    if not isinstance(schema, dict):
        return ""
    if "$ref" in schema:
        if depth > 1:
            return schema["$ref"].split("/")[-1]
        node = components
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        return _schema_brief(node, components, depth + 1)
    t = schema.get("type", "")
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        items = []
        for pname, p in list(props.items())[:40]:
            pt = p.get("type", "") if isinstance(p, dict) else ""
            if isinstance(p, dict) and "$ref" in p:
                pt = p["$ref"].split("/")[-1]
            items.append(f"{pname}: {pt}".strip())
        more = f" …+{len(props)-40} more" if len(props) > 40 else ""
        return "object { " + ", ".join(items) + more + " }"
    if t == "array":
        return f"array<{_schema_brief(schema.get('items', {}), components, depth+1)}>"
    if "enum" in schema:
        return f"{t} enum {schema['enum'][:8]}"
    return t


def _clean_html(s: str) -> str:
    s = _TAG_RE.sub(" ", s or "")
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"[ \t]+", " ", s).strip()


def _spec_to_markdown(spec: dict, meta: dict) -> str:
    info = spec.get("info", {})
    lines = [
        f"# {info.get('title', meta['service'])} {meta['version']} — Workday REST API ({meta['tier']})",
        "",
        _clean_html(info.get("description", "")),
        "",
        f"- Service: `{meta['service']}` | basePath: `{meta['basePath']}` | routing: {meta['routing']}",
        f"- Spec: {OPENAPI_BASE}/{meta['specFilePath']}",
        "",
    ]
    components = spec.get("components", {})
    servers = spec.get("servers") or []
    if servers:
        lines += ["## Servers", "", *[f"- `{s.get('url','')}`" for s in servers], ""]
    for path, item in (spec.get("paths") or {}).items():
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            op_id = op.get("operationId", "")
            lines.append(f"## {method.upper()} {path}")
            lines.append("")
            if op.get("summary"):
                lines.append(f"**{op['summary']}**  ")
            if op.get("description"):
                lines.append(_clean_html(op["description"])[:2000])
            lines.append("")
            if op_id:
                lines.append(f"- operationId: `{op_id}`")
            if op.get("wid"):
                lines.append(f"- WID: `{op['wid']}`")
            try_flag = "yes" if op.get("tryEnabled") else "no"
            lines.append(f"- try-enabled in explorer: {try_flag}")
            lines.append("")
            params = op.get("parameters") or []
            if params:
                lines.append("### Parameters")
                lines.append("")
                lines.append("| name | in | type | required | description |")
                lines.append("|------|----|------|----------|-------------|")
                for p in params[:60]:
                    schema = p.get("schema") or {}
                    ptype = schema.get("type", "")
                    if "$ref" in schema:
                        ptype = _schema_brief(schema, components)
                    desc = _clean_html(p.get("description", ""))[:300].replace("|", "\\|")
                    lines.append(
                        f"| `{p.get('name','')}` | {p.get('in','')} | {ptype} | {p.get('required', False)} | {desc} |"
                    )
                lines.append("")
            rb = op.get("requestBody")
            if rb:
                lines.append("### Request body")
                lines.append("")
                for ct, media in (rb.get("content") or {}).items():
                    lines.append(f"- `{ct}`: {_schema_brief(media.get('schema', {}), components)[:1500]}")
                lines.append("")
            for code, resp in (op.get("responses") or {}).items():
                if code not in ("200", "201", "default"):
                    continue
                content = resp.get("content") or {}
                briefs = [
                    f"`{ct}`: {_schema_brief(m.get('schema', {}), components)[:1500]}"
                    for ct, m in content.items()
                ]
                if briefs:
                    lines.append(f"### Response {code}")
                    lines.append("")
                    lines += [f"- {b}" for b in briefs]
                    lines.append("")
    return "\n".join(lines)


def _changelog_to_markdown(cl: dict, meta: dict) -> str:
    info = cl.get("info", {})
    lines = [
        f"# {info.get('title', meta['service'])} {meta['version']} — change log (Workday REST)",
        "",
        "Release-stamped endpoint changes, newest release first. Path prefix is the release (e.g. `2026.13`).",
        "",
    ]
    for path, item in (cl.get("paths") or {}).items():
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            lines.append(f"## {path} — {method.upper()}")
            lines.append("")
            if op.get("summary"):
                lines.append(f"**{op['summary']}**")
            desc = _clean_html(op.get("description", ""))
            if desc:
                lines.append(desc[:800])
            for p in op.get("parameters") or []:
                pdesc = _clean_html(p.get("description", ""))
                if pdesc:
                    lines.append(f"- `{p.get('name','')}` ({p.get('in','')}): {pdesc[:300]}")
            lines.append("")
    return "\n".join(lines)


def _sync_rest(manifest: dict) -> dict:
    cat_res = fetch_cached(REST_CATALOG_URL)
    if cat_res.status != 200:
        raise RuntimeError(f"services_oas3.json fetch failed: {cat_res.error or cat_res.status}")
    catalog = json.loads(cat_res.text)
    cat_path = _raw_dir() / "services_oas3.json"
    cat_path.write_text(cat_res.text)

    services = []  # (tier, svc dict)
    for tier in ("productionConfidenceLevel", "previewConfidenceLevel",
                 "experimentalServices", "archivedServices"):
        for svc in catalog.get(tier, []):
            services.append((tier, svc))

    jobs = []
    for tier, svc in services:
        for fkey in ("specFilePath", "changeLogFilePath"):
            f = svc.get(fkey)
            if f:
                jobs.append((f"{OPENAPI_BASE}/{f}", manifest.get(f"{OPENAPI_BASE}/{f}", {}).get("etag", "")))
    results = fetch_all(jobs)
    # fetch_all returns completion-ordered results — key by res.url, never zip
    # positionally (asor-into-absenceManagement bug, 2026-10-15)
    bodies = {}
    for res in results:
        if res.status == 200:
            try:
                bodies[res.url] = json.loads(res.text)
            except json.JSONDecodeError as e:
                bodies[res.url] = None
                print(f"  ! bad JSON {res.url}: {e}")

    changed, errors = 0, []
    for tier, svc in services:
        base = dict(
            service=svc.get("urlServiceName", ""),
            version=svc.get("version", ""),
            basePath=svc.get("basePath", ""),
            routing=svc.get("routing", ""),
            tier=tier,
        )
        for fkey, kind, sub, mk in (
            ("specFilePath", "rest-spec", "rest", _spec_to_markdown),
            ("changeLogFilePath", "rest-changelog", "rest-changelog", _changelog_to_markdown),
        ):
            f = svc.get(fkey)
            if not f:
                continue
            base["specFilePath"] = f  # referenced by the markdown templates
            url = f"{OPENAPI_BASE}/{f}"
            spec = bodies.get(url)
            if spec is None:
                if url in bodies:
                    errors.append(f"{url}: unparseable JSON")
                continue  # 304: unchanged, manifest row already good
            md = mk(spec, base)
            rel = f"workday/{sub}/{f[:-5]}.md" if f.endswith(".json") else f"workday/{sub}/{f}.md"
            (corpus_dir(SOURCE) / sub).mkdir(parents=True, exist_ok=True)
            (corpus_dir(SOURCE) / rel.removeprefix("workday/")).write_text(md)
            manifest[url] = {
                "url": url,
                "path": rel,
                "sha256": sha256_text(md),
                "title": f"{svc.get('displayName', base['service'])} {base['version']} — "
                + ("spec" if kind == "rest-spec" else "changelog"),
                "section": f"REST API ({tier})",
                "kind": kind,
                "service": base["service"],
                "version": base["version"],
                "tier": tier,
            }
            changed += 1

    # services index doc
    idx = ["# Workday REST API services index", "",
           f"From `services_oas3.json` ({len(services)} services across 4 confidence tiers). "
           "Per-service endpoint specs live in sibling pages.", ""]
    for tier, svc in services:
        idx.append(
            f"## {svc.get('displayName', '')} ({svc.get('version','')}) — {tier}\n\n"
            f"- urlServiceName: `{svc.get('urlServiceName','')}` | basePath: `{svc.get('basePath','')}` | routing: {svc.get('routing','')}\n\n"
            f"{_clean_html(svc.get('description',''))[:600]}\n"
        )
    idx_rel = "workday/rest/_services.md"
    (corpus_dir(SOURCE) / "rest").mkdir(parents=True, exist_ok=True)
    (corpus_dir(SOURCE) / "rest/_services.md").write_text("\n".join(idx))
    idx_url = f"{BASE}/rest-api-explorer/overview"
    manifest[idx_url] = {
        "url": idx_url,
        "path": idx_rel,
        "sha256": sha256_text("\n".join(idx)),
        "title": "Workday REST API services index",
        "section": "REST API",
        "kind": "rest-index",
    }
    return {"services": len(services), "changed": changed, "errors": errors[:10]}


# ---------------------------------------------------------------- SOAP ----


def _sync_soap(manifest: dict) -> dict:
    # Explorer loads numbered snapshots; .175 verified current 2026-10-15
    # (soap_services.176+ are 404). ETag cache handles a future bump: probe a
    # couple of candidate numbers and fall back to the known one.
    snap = "175"
    out = {"snapshot": snap, "changed": 0, "errors": []}
    files = {}
    for name in ("soap_services", "soap_versions"):
        res = fetch_cached(f"{SOAP_BASE}/{name}.{snap}.json")
        if res.status != 200:
            raise RuntimeError(f"{name}.{snap}.json fetch failed: {res.error or res.status}")
        (_raw_dir() / f"{name}.{snap}.json").write_text(res.text)
        files[name] = json.loads(res.text)

    # the snapshot ships empty `documentation`; the legacy versionless catalog
    # (v40.1) carries the service descriptions — merge them in
    legacy_docs: dict[str, str] = {}
    legacy = fetch_cached(f"{SOAP_BASE}/soap_services.json")
    if legacy.status == 200:
        for ver_services in json.loads(legacy.text).values():
            for s in ver_services:
                if s.get("documentation", "").strip():
                    legacy_docs[s.get("name", "")] = s["documentation"]

    versions = files["soap_versions"]
    services_by_ver = files["soap_services"]
    vers_md = ["# Workday SOAP API versions", "",
               f"Snapshot `{snap}`. `{len(versions)}` versions, newest first.", "",
               "| version | released to production |", "|---------|------------------------|"]
    for v in versions:
        vers_md.append(f"| {v.get('versionId','')} | {v.get('releaseToProductionDate','')[:10]} |")
    vers_text = "\n".join(vers_md)
    (corpus_dir(SOURCE) / "soap").mkdir(parents=True, exist_ok=True)
    (corpus_dir(SOURCE) / "soap/_versions.md").write_text(vers_text)
    v_url = f"{SOAP_BASE}/soap_versions.{snap}.json"
    manifest[v_url] = {
        "url": v_url, "path": "workday/soap/_versions.md", "sha256": sha256_text(vers_text),
        "title": "Workday SOAP API versions", "section": "SOAP API", "kind": "soap-versions",
    }
    out["changed"] += 1

    for ver, services in services_by_ver.items():
        for svc in services:
            name = svc.get("name", "")
            doc = _clean_html(svc.get("documentation", "") or legacy_docs.get(name, ""))
            ops = svc.get("operations") or []
            md = [
                f"# {name} ({ver}) — Workday SOAP web service",
                "",
                doc,
                "",
                f"- version: `{ver}` | public: {svc.get('isPublic', '')}",
                f"- `{len(ops)}` operations:",
                "",
            ]
            md += [f"- `{o if isinstance(o, str) else o.get('name','')}`" for o in ops]
            text = "\n".join(md)
            rel = f"workday/soap/service/{name}_{ver}.md"
            (corpus_dir(SOURCE) / "soap/service").mkdir(parents=True, exist_ok=True)
            (corpus_dir(SOURCE) / rel.removeprefix("workday/")).write_text(text)
            url = f"{SOAP_BASE}/soap_services.{snap}.json#{ver}/{name}"
            manifest[url] = {
                "url": url, "path": rel, "sha256": sha256_text(text),
                "title": f"{name} ({ver}) — SOAP web service", "section": f"SOAP API ({ver})",
                "kind": "soap-service", "service": name, "version": ver,
            }
            out["changed"] += 1
    return out


# ---------------------------------------------------------------- Graph ----


def _sync_graph(manifest: dict) -> dict:
    res = fetch_cached(GRAPH_SCHEMA_URL)
    if res.status not in (200, 304):
        raise RuntimeError(f"schema.txt fetch failed: {res.error or res.status}")
    if res.status == 200:
        text = res.text
        header = (
            "# Workday Graph API — GraphQL schema\n\n"
            "The Graph API exposes Workday business objects as a GraphQL graph — read-oriented "
            "queries over connected objects (workers, organizations, etc.). This is the full schema.\n\n"
            "```graphql\n"
        )
        (corpus_dir(SOURCE) / "graph").mkdir(parents=True, exist_ok=True)
        (corpus_dir(SOURCE) / "graph/schema.md").write_text(header + text + "\n```")
        manifest[GRAPH_SCHEMA_URL] = {
            "url": GRAPH_SCHEMA_URL, "path": "workday/graph/schema.md", "sha256": sha256_text(text),
            "title": "Workday Graph API — GraphQL schema", "section": "Graph API", "kind": "graph-schema",
        }
        return {"changed": 1}
    return {"changed": 0}  # 304


# ------------------------------------------------------------ WQL aliases ----


def _sync_wql_aliases(manifest: dict) -> dict:
    """Tenant WQL alias export (xlsx, 427K rows) -> one markdown doc per data
    source + a SQLite DB for exact lookups. RAG answers 'which data source has
    field X'; the DB answers exact alias lookups fast."""
    if not WQL_XLSX.exists():
        return {"skipped": f"missing {WQL_XLSX}"}
    import openpyxl

    wb = openpyxl.load_workbook(WQL_XLSX, read_only=True)
    ws = wb["Sheet1"]
    rows = ws.iter_rows(values_only=True)
    # header rows: title, blank, then 'Data Source|WQL Alias|Field|WQL Alias'
    for _ in range(3):
        next(rows)
    sources: dict[str, dict] = {}  # ds_alias -> {name, fields: [(field, alias)]}
    for ds_name, ds_alias, field, field_alias in rows:
        if not ds_alias:
            continue
        d = sources.setdefault(ds_alias, {"name": ds_name or "", "fields": []})
        if field:
            d["fields"].append((str(field), str(field_alias or "")))
    wb.close()

    # SQLite for exact lookups
    import sqlite3

    db_path = corpus_dir(SOURCE) / "_raw" / "wql-aliases.db"
    db = sqlite3.connect(db_path)
    db.executescript(
        """
        DROP TABLE IF EXISTS fields; DROP TABLE IF EXISTS datasources;
        CREATE TABLE datasources (ds_alias TEXT PRIMARY KEY, ds_name TEXT);
        CREATE TABLE fields (ds_alias TEXT, field TEXT, field_alias TEXT);
        CREATE INDEX idx_fields_alias ON fields(field_alias);
        CREATE INDEX idx_fields_text ON fields(field);
        """
    )
    db.executemany("INSERT INTO datasources VALUES (?,?)", [(a, d["name"]) for a, d in sources.items()])
    db.executemany(
        "INSERT INTO fields VALUES (?,?,?)",
        [(a, f, fa) for a, d in sources.items() for f, fa in d["fields"]],
    )
    db.commit()
    db.close()

    changed = 0
    (corpus_dir(SOURCE) / "wql-alias").mkdir(parents=True, exist_ok=True)
    for alias, d in sorted(sources.items()):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", alias)
        md = [
            f"# WQL data source: {d['name']} (`{alias}`)",
            "",
            f"`{len(d['fields'])}` report fields. FROM clause alias: `{alias}`.",
            "",
            "| Field | WQL alias |", "|-------|-----------|",
        ]
        md += [f"| {f} | `{fa}` |" for f, fa in d["fields"]]
        text = "\n".join(md)
        rel = f"workday/wql-alias/{safe}.md"
        (corpus_dir(SOURCE) / rel.removeprefix("workday/")).write_text(text)
        manifest[f"workday://wql-alias/{alias}"] = {
            "url": f"workday://wql-alias/{alias}", "path": rel, "sha256": sha256_text(text),
            "title": f"WQL data source: {d['name']} ({alias})",
            "section": "WQL tenant aliases", "kind": "wql-alias", "service": alias,
        }
        changed += 1
    return {"data_sources": len(sources), "changed": changed}


# ---------------------------------------------------------------- main ----


def sync() -> dict:
    manifest = load_manifest(SOURCE)
    summary: dict = {}
    for name, fn in (
        ("docs", lambda: _sync_docs(manifest)),
        ("rest", lambda: _sync_rest(manifest)),
        ("soap", lambda: _sync_soap(manifest)),
        ("graph", lambda: _sync_graph(manifest)),
        ("wql-aliases", lambda: _sync_wql_aliases(manifest)),
    ):
        print(f"[workday] {name} ...", flush=True)
        try:
            summary[name] = fn()
        except Exception as e:  # noqa: BLE001 - one surface failing must not kill the rest
            summary[name] = {"error": str(e)}
        # persist after every surface: a mid-run crash must not lose the ETags
        save_manifest(SOURCE, manifest)
    return summary


if __name__ == "__main__":
    import pprint

    pprint.pprint(sync())
