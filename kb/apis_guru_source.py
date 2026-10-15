"""APIs.guru catalog source: ONE metadata doc per API (~2.5k), never spec dumps.

api.apis.guru/v2/list.json is the OpenAPI-directory index. Top-level keys are
api-ids ("googleapis.com:books"); values carry the preferred version plus
per-version info (title, description, x-apisguru-categories, contact). The
directory is the value: full OpenAPI specs are LINKED only —
https://api.apis.guru/v2/specs/<api-id with ':' as '/'>/<version>.json — and
never downloaded (2.5k spec dumps would explode the corpus).

    ~/Documents/workato-kb/.venv/bin/python -m kb.apis_guru_source
"""

from __future__ import annotations

import json
import re

from .common import corpus_dir, fetch_cached, load_manifest, save_manifest, sha256_text

SOURCE = "apis-guru"
LIST_URL = "https://api.apis.guru/v2/list.json"
API_PAGE = "https://apis.guru/{}"


def _slug(text: str) -> str:
    # api-ids are "provider[:service]"; ':' is the only path-hostile char
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "api"


def _split(api_id: str) -> tuple[str, str]:
    provider, sep, name = api_id.partition(":")
    return provider, (name if sep else "")


def _pick_version(entry: dict) -> tuple[str, dict]:
    """(version, version-entry) for the preferred version, first listed fallback."""
    versions = entry.get("versions") or {}
    pref = entry.get("preferred")
    if pref and pref in versions:
        return pref, versions[pref]
    for ver, v in sorted(versions.items()):
        return ver, v
    return "", {}


def _title_of(api_id: str, entry: dict) -> str:
    _, vend = _pick_version(entry)
    return ((vend.get("info") or {}).get("title") or "").strip() or api_id


def _doc(api_id: str, entry: dict) -> str | None:
    """Render the catalog markdown for one API, or None for empty junk."""
    ver, vend = _pick_version(entry)
    info = (vend or {}).get("info") or {}
    title = (info.get("title") or "").strip()
    desc = (info.get("description") or "").strip()
    if not title and not desc:
        return None  # truly empty junk: no title AND no description
    title = title or api_id

    # description may live on a non-preferred version even when the preferred
    # one has none
    if not desc:
        for v in (entry.get("versions") or {}).values():
            d = ((v.get("info") or {}).get("description") or "").strip()
            if d:
                desc = d
                break

    cats = info.get("x-apisguru-categories") or []
    contact = info.get("contact") or {}
    license_ = info.get("license") or {}
    n_versions = len(entry.get("versions") or {})
    spec_url = vend.get("link") or vend.get("swaggerUrl") or ""

    lines = [
        f"# {title}",
        "",
        f"API directory entry for `{api_id}` on [apis.guru]({API_PAGE.format(api_id)}).",
        "",
        desc,
        "",
    ]
    facts: list[tuple[str, str]] = [
        ("api id", f"`{api_id}`"),
        ("preferred version", ver or "(none listed)"),
        ("versions listed", str(n_versions)),
    ]
    if cats:
        facts.append(("categories", ", ".join(cats)))
    if contact.get("name"):
        facts.append(("contact", contact["name"]))
    if contact.get("url"):
        facts.append(("contact url", contact["url"]))
    if license_.get("name"):
        facts.append(("license", license_["name"]))
    if entry.get("added"):
        facts.append(("added to directory", str(entry["added"])[:10]))
    if vend.get("updated"):
        facts.append(("spec updated", str(vend["updated"])[:10]))
    for k, v in facts:
        lines.append(f"- **{k}:** {v}")
    if spec_url:
        lines += [
            "",
            f"- OpenAPI spec (linked, not ingested): {spec_url}",
            f"- Raw spec pattern: https://api.apis.guru/v2/specs/"
            f"{api_id.replace(':', '/')}/{ver}.json",
        ]
    lines.append("")
    return "\n".join(lines)


def sync() -> dict:
    manifest = load_manifest(SOURCE)
    out_dir = corpus_dir(SOURCE)
    out_dir.mkdir(parents=True, exist_ok=True)

    # one blob, no per-row etags: always a plain GET (fetch_cached only 304s
    # when handed an etag, which we never have here)
    lst = fetch_cached(LIST_URL)
    if lst.status != 200:
        raise RuntimeError(f"apis.guru list fetch failed: {lst.error}")
    apis = json.loads(lst.text)

    changed = unchanged = 0
    seen: set[str] = set()
    used_names: set[str] = set()
    used_ci: set[str] = set()  # macOS FS is case-insensitive: guard case variants
    for api_id, entry in apis.items():
        md = _doc(api_id, entry)
        if md is None:
            continue
        url = f"apis-guru://{api_id}"
        seen.add(url)
        digest = sha256_text(md)
        provider, name = _split(api_id)
        base = _slug(f"{provider}_{name}") if name else _slug(provider)
        fname = f"{base}.md"
        n = 0
        while fname in used_names or fname.lower() in used_ci:
            n += 1
            fname = f"{base}_{n}.md"
        used_names.add(fname)
        used_ci.add(fname.lower())
        rec = manifest.get(url, {})
        if rec.get("sha256") == digest and rec.get("path") == f"{SOURCE}/{fname}":
            unchanged += 1
            continue
        (out_dir / fname).write_text(md)
        manifest[url] = {
            "url": url,
            "path": f"{SOURCE}/{fname}",
            "sha256": digest,
            "title": _title_of(api_id, entry),
            "source": SOURCE,
        }
        changed += 1

    # prune docs for APIs that left the directory
    stale = [u for u in manifest if u not in seen]
    for u in stale:
        rel = manifest[u].get("path", "").split("/", 1)[-1]
        if rel:
            (out_dir / rel).unlink(missing_ok=True)
        del manifest[u]

    save_manifest(SOURCE, manifest)
    return {
        "source": SOURCE,
        "apis": len(apis),
        "docs": len(manifest),
        "changed": changed,
        "unchanged": unchanged,
        "pruned": len(stale),
    }


if __name__ == "__main__":
    print(json.dumps(sync(), indent=2))
