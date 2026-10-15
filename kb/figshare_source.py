"""Figshare source: "Information and computing sciences" research outputs.

API reality (probed 2026-10-15, public unauthenticated API):
- POST /v2/articles/search IGNORES category filters ("category_id",
  "categories" return results identical to unfiltered search — the wave-1
  warning was correct). Category-scoped search is not available unauthenticated.
- Offset pagination caps at 1000: page_size=1000 x 2 pages = a 2000-item
  window; that window is the full reachable catalog per query.
- GET /v2/articles/{id} carries the real categories (some articles have
  empty category lists) plus description/DOI/license.
- No X-RateLimit-* headers are served; the docs only ask for ~1 req/s, so
  the detail fetch self-throttles instead of trusting response headers.

Design: harvest the recent title catalog (cheap, 2 requests), then
detail-fetch a bounded newest-first slice (DETAIL_CAP/run, self-throttled),
keep only articles whose detail categories intersect the descendant set of
the ICS root (28798). Only verified articles emit docs; scanned ids are
remembered so the detail window advances night over night. Licensing of
research outputs varies: internal-RAG-only, never republish.

    ~/Documents/workato-kb/.venv/bin/python -m kb.figshare_source
"""

from __future__ import annotations

import json
import re
import time
import urllib.request

from .common import corpus_dir, load_manifest, save_manifest, sha256_text

SOURCE = "figshare"
API = "https://api.figshare.com/v2"
ROOT_CATEGORY = 28798  # Information and computing sciences
DETAIL_CAP = 800  # detail fetches per sync (~14 min at 1 rps)
PAGE_SIZE = 1000  # API max; offset cap 1000 -> 2 pages per query
CATALOG_KEY = f"{SOURCE}://catalog"
MAX_DESC = 4000  # chars of stripped description kept per article doc

RAW = corpus_dir(SOURCE) / "_raw"


def _post(path: str, body: dict) -> tuple[dict, dict]:
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r), dict(r.headers)


def _get(path: str) -> tuple[dict, dict]:
    with urllib.request.urlopen(f"{API}{path}", timeout=60) as r:
        return json.load(r), dict(r.headers)


def _descendants(root: int) -> set[int]:
    cats, _ = _get("/categories")
    kids: dict[int, list[int]] = {}
    for c in cats:
        if c.get("parent_id"):
            kids.setdefault(c["parent_id"], []).append(c["id"])
    out, stack = {root}, [root]
    while stack:
        for cid in kids.get(stack.pop(), []):
            if cid not in out:
                out.add(cid)
                stack.append(cid)
    return out


def _catalog() -> list[dict]:
    """Recent title catalog: 2 pages x 1000 (the API's reachable window)."""
    rows: list[dict] = []
    seen: set[int] = set()
    for page in (1, 2):
        arts, _ = _post(
            "/articles/search",
            {"page_size": PAGE_SIZE, "page": page,
             "order": "published_date", "order_direction": "desc"},
        )
        for a in arts:
            if a["id"] not in seen:
                seen.add(a["id"])
                rows.append(a)
    return rows


def _slug(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", (title or "").lower()).strip("-")
    return s[:60] or "untitled"


def sync() -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    descendants = _descendants(ROOT_CATEGORY)

    manifest = load_manifest(SOURCE)
    out_articles = corpus_dir(SOURCE) / "articles"
    out_articles.mkdir(parents=True, exist_ok=True)

    scanned_path = RAW / "scanned_ids.json"
    scanned: set[int] = set(json.loads(scanned_path.read_text())) if scanned_path.exists() else set()
    # already-verified articles need no re-detail
    for u in manifest:
        if u != CATALOG_KEY:
            scanned.add(int(u.rsplit("/", 1)[-1]))

    catalog = _catalog()
    (RAW / "catalog.json").write_text(json.dumps(catalog))

    # detail slice: newest unscanned, throttled ~1 rps (docs ask for 1 req/s;
    # probed 2026-10-15: no X-RateLimit-* headers exist to size against)
    todo = [a["id"] for a in catalog if a["id"] not in scanned][:DETAIL_CAP]
    rate_hdrs: dict[str, str] = {}
    details: dict[int, dict] = {}
    for i, aid in enumerate(todo, 1):
        try:
            det, hdrs = _get(f"/articles/{aid}")
            details[aid] = det
            found = {k: v for k, v in hdrs.items() if "rate" in k.lower()}
            if found:
                rate_hdrs.update(found)
        except Exception:  # noqa: BLE001 - deleted/embargoed items: mark, move on
            pass
        scanned.add(aid)
        if i % 100 == 0:
            print(f"  ... {i}/{len(todo)} details", flush=True)
        time.sleep(1.0)
    scanned_path.write_text(json.dumps(sorted(scanned)))

    # emit docs for category-verified articles
    changed_articles = 0
    for aid, det in details.items():
        det_cats = {c["id"] for c in det.get("categories") or []}
        det_cats |= {
            c["id"] for c in det.get("custom_categories") or [] if isinstance(c, dict)
        }
        if not det_cats & descendants:
            continue
        url = f"figshare://{aid}"
        title = det.get("title") or f"figshare article {aid}"
        authors = ", ".join(
            (a.get("full_name") or "") for a in det.get("authors") or []
        )
        desc = (det.get("description") or "").strip()
        doi = det.get("doi") or ""
        lic = (det.get("license") or {}).get("name") or ""
        landing = det.get("figshare_url") or f"https://figshare.com/articles/{aid}"
        lines = [
            f"# {title}",
            "",
            "Figshare research output, category-verified into the Information "
            "and computing sciences subtree. Internal-RAG-only; licensing "
            f"varies per item ({lic or 'unspecified'}) — never republish.",
            "",
            f"- **authors:** {authors or '(none listed)'}",
            f"- **published:** {det.get('published_date', '')[:10]}",
            f"- **DOI/link:** {f'https://doi.org/{doi}' if doi else landing}",
            f"- **license:** {lic or 'unspecified'}",
            f"- **figshare page:** {landing}",
            "",
        ]
        if desc:
            plain = re.sub(r"<[^>]+>", " ", desc)
            plain = re.sub(r"\s+", " ", plain).strip()[:MAX_DESC]
            lines.append(plain)
            lines.append("")
        md = "\n".join(lines)
        digest = sha256_text(md)
        rec = manifest.get(url, {})
        if rec.get("sha256") == digest:
            continue
        fname = f"{aid}-{_slug(title)}.md"
        (out_articles / fname).write_text(md)
        manifest[url] = {
            "url": url,
            "path": f"{SOURCE}/articles/{fname}",
            "sha256": digest,
            "title": title,
            "source": SOURCE,
            "published_date": det.get("published_date", "")[:10],
            "license": lic,
            "doi": doi,
        }
        changed_articles += 1

    verified_total = sum(1 for u in manifest if u != CATALOG_KEY)

    # catalog.md: everything harvested (accumulated verified set), title rows
    rows = [
        (u, manifest[u])
        for u in sorted(manifest, key=lambda x: int(x.rsplit("/", 1)[-1]))
        if u != CATALOG_KEY
    ]
    lines = [
        "# Figshare: Information and computing sciences (28798) harvest",
        "",
        f"Category-verified figshare research outputs under the ICS subtree "
        f"(root {ROOT_CATEGORY}, {len(descendants)} descendant categories). The "
        "public search API ignores category filters (verified 2026-10-15), so "
        "items are verified at detail-fetch time; the detail budget is capped "
        f"at {DETAIL_CAP} fetches/run at ~1 req/s. Licensing varies per item: "
        "internal-RAG-only, never republish.",
        "",
        f"**{verified_total} verified articles** | {len(catalog)} titles in the "
        f"recent catalog window | {len(todo)} details fetched this run.",
        "",
        "| article | published | doi/link | license |",
        "| --- | --- | --- | --- |",
    ]
    for u, r in rows:
        aid = u.rsplit("/", 1)[-1]
        rel = r["path"].split("/", 1)[-1]
        link = f"https://doi.org/{r['doi']}" if r.get("doi") else f"https://figshare.com/articles/{aid}"
        lines.append(
            f"| [{r['title']}]({rel}) | {r.get('published_date', '')} "
            f"| {link} | {r.get('license') or 'unspecified'} |"
        )
    lines.append("")
    catalog_md = "\n".join(lines)
    cat_path = corpus_dir(SOURCE) / "catalog.md"
    if not cat_path.exists() or cat_path.read_text() != catalog_md:
        cat_path.write_text(catalog_md)
    manifest[CATALOG_KEY] = {
        "url": CATALOG_KEY,
        "path": f"{SOURCE}/catalog.md",
        "sha256": sha256_text(catalog_md),
        "title": "Figshare: Information and computing sciences harvest",
        "source": SOURCE,
    }

    save_manifest(SOURCE, manifest)
    return {
        "source": SOURCE,
        "catalog_titles": len(catalog),
        "details_fetched": len(todo),
        "verified_this_run": sum(1 for aid in details if f"figshare://{aid}" in manifest),
        "verified_total": verified_total,
        "detail_cap": DETAIL_CAP,
        "rate_limit_headers": rate_hdrs,
        "changed": changed_articles,
        "descendant_categories": len(descendants),
    }


if __name__ == "__main__":
    print(json.dumps(sync(), indent=2))
