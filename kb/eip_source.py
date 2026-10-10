"""Enterprise Integration Patterns source (enterpriseintegrationpatterns.com).

Walks the index page for .html links (patterns/, ramblings/, top-level articles),
converts each page to markdown. Static hosting with ETags -> cheap conditional sync.

    python3 -m kb.eip_source
"""

from __future__ import annotations

import json
import re

from .common import corpus_dir, fetch_cached, load_manifest, save_manifest, sha256_text
from .wp_source import html_to_markdown

BASE = "https://www.enterpriseintegrationpatterns.com"
INDEX = f"{BASE}/"

_INCLUDE = re.compile(r"^/(patterns/|ramblings/|examples?/)|^/[a-z0-9_-]+\.html$")
_EXCLUDE = ("/gregor.html", "/books1.html", "/books2.html", "/downloads.html", "/articles.html", "/search", "/faq")

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)


def _page_title(html: str, url: str) -> str:
    m = _H1_RE.search(html) or _TITLE_RE.search(html)
    if not m:
        return url.rstrip("/").rsplit("/", 1)[-1]
    t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return t.split("|")[0].split(" - ")[0].strip()


def _links(html: str) -> list[str]:
    hrefs = re.findall(r'href="([^"#?]+\.html)"', html)
    out = set()
    for h in hrefs:
        if h.startswith("/"):
            if _INCLUDE.match(h) and not any(h.startswith(x) for x in _EXCLUDE):
                out.add(BASE + h)
    return sorted(out)


def sync() -> dict:
    source = "eip"
    manifest = load_manifest(source)
    out_dir = corpus_dir(source)
    out_dir.mkdir(parents=True, exist_ok=True)

    idx = fetch_cached(INDEX)
    if idx.status != 200:
        raise RuntimeError(f"EIP index fetch failed: {idx.error}")
    urls = _links(idx.text)

    changed = unchanged = 0
    errors = []
    for url in urls:
        res = fetch_cached(url)
        if res.status != 200:
            errors.append({"url": url, "error": f"HTTP {res.status}"})
            continue
        digest = sha256_text(res.text)
        rec = manifest.get(url, {})
        if rec.get("sha256") == digest:
            unchanged += 1
            continue
        title = _page_title(res.text, url)
        slug = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html")
        md = (
            "---\n"
            f'url: "{url}"\n'
            f'title: "{title.replace(chr(34), chr(39))}"\n'
            f'source: "eip"\n'
            "---\n\n" + html_to_markdown(res.text)
        )
        (out_dir / f"{slug}.md").write_text(md)
        manifest[url] = {
            "url": url,
            "path": f"eip/{slug}.md",
            "sha256": digest,
            "title": title,
            "etag": res.etag,
        }
        changed += 1

    save_manifest(source, manifest)
    return {"source": source, "pages": len(urls), "changed": changed, "unchanged": unchanged, "errors": errors}


if __name__ == "__main__":
    print(json.dumps(sync(), indent=2))
