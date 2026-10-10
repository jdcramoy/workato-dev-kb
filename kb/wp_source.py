"""WordPress sources (blog, the-connector) + product-hub changelog crawler.

- blog/connector: wp-json wp/v2/posts pagination; content.rendered -> markdown via html2text.
- changelog: backfill via /page/N/ archive listing, incremental via /feed/ RSS.
No ETags on wp-json, so change detection = sha256 of content.
"""

from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .common import UA, corpus_dir, fetch_cached, load_manifest, save_manifest, sha256_text

# One WP install (blog2workato.wpengine.com) hosts both workato.com/blog and
# /the-connector — /blog/wp-json and /the-connector/wp-json both serve the same
# 1,197-post collection. Crawl the origin host once; source = "blog".
BLOG_API = "https://blog2workato.wpengine.com/wp-json/wp/v2/posts"
CHANGELOG_API = "https://www.workato.com/product-hub/wp-json/wp/v2/posts?categories=443"  # "What's New" = changelog (252 posts)
CHANGELOG_BASE = "https://www.workato.com/product-hub/changelog"
CHANGELOG_FEED = f"{CHANGELOG_BASE}/feed/"

_SLUG_RE = re.compile(r"/([^/?#]+)/?$")


def slug_of(url: str) -> str:
    m = _SLUG_RE.search(url.rstrip("/"))
    return m.group(1) if m else sha256_text(url)[:16]


def html_to_markdown(html: str) -> str:
    try:
        import html2text

        h = html2text.HTML2Text()
        h.body_width = 0
        h.ignore_links = False
        h.ignore_images = True
        h.ignore_tables = False
        return h.handle(html).strip()
    except ImportError:
        # minimal stdlib fallback until html2text is installed in the venv
        text = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.S | re.I)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
        text = re.sub(r"</p>", "\n\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        import html as htmllib

        return htmllib.unescape(text).strip()


def frontmatter(title: str, url: str, date: str, source: str, categories: str) -> str:
    def esc(s: str) -> str:
        return s.replace('"', "'")[:200]

    return (
        "---\n"
        f'url: "{esc(url)}"\n'
        f'title: "{esc(title)}"\n'
        f'date: "{esc(date)}"\n'
        f'source: "{source}"\n'
        f'categories: "{esc(categories)}"\n'
        "---\n\n"
    )


def sync_wp(api_url: str, source: str, max_pages: int = 100) -> dict:
    """Backfill/sync one WordPress post collection. Change detect via sha256."""
    manifest = load_manifest(source)
    out_dir = corpus_dir(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    seen = set()
    changed, unchanged, errors = 0, 0, []

    for page in range(1, max_pages + 1):
        sep = "&" if "?" in api_url else "?"
        url = f"{api_url}{sep}per_page=50&page={page}&_fields=id,link,date,modified,title,content,categories"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                posts = json.load(resp)
        except Exception as e:  # noqa: BLE001
            if page == 1:
                errors.append({"url": url, "error": str(e)})
            break  # past-the-end page = done
        if not posts:
            break
        for post in posts:
            link = post["link"]
            seen.add(link)
            title = re.sub("<[^>]+>", "", post.get("title", {}).get("rendered", ""))
            html = post.get("content", {}).get("rendered", "")
            digest = sha256_text(html)
            rec = manifest.get(link, {})
            if rec.get("sha256") == digest:
                unchanged += 1
                continue
            md = frontmatter(
                title, link, post.get("date", ""), source, str(post.get("categories", ""))
            ) + html_to_markdown(html)
            body = out_dir / f"{slug_of(link)}.md"
            body.write_text(md)
            rec.update(
                {
                    "url": link,
                    "path": f"{source}/{body.name}",
                    "sha256": digest,
                    "title": title.strip(),
                    "date": post.get("date", ""),
                }
            )
            manifest[link] = rec
            changed += 1

    removed = sorted(set(manifest) - seen)
    save_manifest(source, manifest)
    return {
        "source": source,
        "changed": changed,
        "unchanged": unchanged,
        "removed": removed,
        "errors": errors,
    }


@dataclass
class FeedItem:
    link: str
    title: str
    date: str


def changelog_backfill() -> dict:
    """Crawl the product-hub WP install, category 443 ("What's New" = changelog)."""
    return sync_wp(CHANGELOG_API, "changelog")


INSPIRATION_API = "https://www.workato.com/product-hub/wp-json/wp/v2/posts?categories=436"  # "Automation Inspiration"


def sync_inspiration() -> dict:
    """Automation Inspiration posts (89): real-world automation patterns/recipes."""
    return sync_wp(INSPIRATION_API, "inspiration")


def changelog_incremental() -> dict:
    """Fetch the RSS feed (10 latest); fetch any post whose sha256 changed."""
    source = "changelog"
    manifest = load_manifest(source)
    res = fetch_cached(CHANGELOG_FEED)
    if res.status != 200:
        return {"source": source, "changed": 0, "unchanged": 0, "removed": [], "errors": ["feed fetch failed"]}
    root = ET.fromstring(res.text)
    items = [
        FeedItem(
            link=item.findtext("link", "").strip(),
            title=(item.findtext("title") or "").strip(),
            date=(item.findtext("pubDate") or "").strip(),
        )
        for item in root.iter("item")
    ]
    changed, unchanged, errors = 0, 0, []
    out_dir = corpus_dir(source)
    for it in items:
        if not it.link or "/changelog/" not in it.link or it.link.rstrip("/") == CHANGELOG_BASE:
            continue
        res = fetch_cached(it.link)
        if res.status != 200:
            errors.append({"url": it.link, "error": f"HTTP {res.status}"})
            continue
        digest = sha256_text(res.text)
        rec = manifest.get(it.link, {})
        if rec.get("sha256") == digest:
            unchanged += 1
            continue
        md = frontmatter(it.title, it.link, it.date, "changelog", "") + html_to_markdown(res.text)
        body = out_dir / f"{slug_of(it.link)}.md"
        body.write_text(md)
        rec.update({"url": it.link, "path": f"changelog/{body.name}", "sha256": digest, "title": it.title})
        manifest[it.link] = rec
        changed += 1
    save_manifest(source, manifest)
    return {"source": source, "changed": changed, "unchanged": unchanged, "removed": [], "errors": errors}


def sync_blog() -> dict:
    return sync_wp(BLOG_API, "blog")
