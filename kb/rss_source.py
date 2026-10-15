"""RSS/Atom feed source: engineering blogs + evergreen essay corpora (scout-expanded
2026-10-10, Josh KB-corpus expansion).

Fetches each feed (etag-cached), parses RSS2/Atom with stdlib ElementTree, converts
full-text entries to markdown via the shared html2text path, and manifests per-post
URLs like every other source. Feeds carrying only summaries (< MIN_BODY chars of
text) are recorded but skipped as too thin — backfill scrapes handle those later.

    python3 -m kb.rss_source            # all feeds
    python3 -m kb.rss_source morning-paper
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from .common import corpus_dir, fetch_cached, load_manifest, save_manifest, sha256_text
from .wp_source import html_to_markdown

FEEDS = {
    # name -> feed URL
    "morning-paper": "https://blog.acolyer.org/feed/",        # ~900 paper summaries, evergreen
    "danluu": "https://danluu.com/atom/index.xml",           # full-site Hugo feed
    "jvns": "https://jvns.ca/atom.xml",
    "simon-willison": "https://simonwillison.net/atom/everything/",
    "latent-space": "https://www.latent.space/feed",
    "chip-huyen": "https://huyenchip.com/feed.xml",
    "eugene-yan": "https://eugeneyan.com/rss/",
    "hf-blog": "https://huggingface.co/blog/feed.xml",
    "anthropic-eng": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_engineering.xml",  # community feed
    "aws-arch-blog": "https://aws.amazon.com/blogs/architecture/feed/",
    "cloudflare": "https://blog.cloudflare.com/rss/",
    "netflix": "https://netflixtechblog.com/feed",
    "fowler-bliki": "https://martinfowler.com/bliki/bliki.atom",
    "lilian-weng": "https://lilianweng.github.io/index.xml",
    "hamel": "https://hamel.dev/index.xml",
    "cynefin": "https://thecynefin.co/feed/",   # Snowden: complexity/decision-making
}

# A full-text entry is at least this many markdown chars; thinner entries are
# summary-only feeds (the entry is recorded in the manifest as `thin` so the
# count is visible, but no corpus file is written).
MIN_BODY = 500

# WordPress-style feeds expose their archive via `?paged=N` (10/page). Backfill
# walks pages until a page adds no new URLs, bounded by BACKFILL_MAX_PAGES.
BACKFILL_CAPABLE = {"morning-paper", "cynefin", "aws-arch-blog"}
BACKFILL_MAX_PAGES = 120

_NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom": "http://www.w3.org/2005/Atom",
}
_TAG = re.compile(r"\{(.+)\}(.+)")


def _local(tag: str) -> str:
    m = _TAG.match(tag)
    return f"{_NS.get(m.group(1), m.group(1))}:{m.group(2)}" if m else tag


def _parse_feed(text: str) -> list[dict]:
    """RSS2 + Atom in one pass: [{title, url, html}]."""
    root = ET.fromstring(text.encode("utf-8"))
    items = []
    if root.tag == "rss":
        items = [
            {
                "title": (it.findtext("title") or "").strip(),
                "url": (it.findtext("link") or "").split("?")[0].strip(),
                "html": (it.findtext("content:encoded", default="", namespaces=_NS) or it.findtext("description") or ""),
            }
            for it in root.iter("item")
        ]
    elif root.tag == f"{{{ _NS['atom'] }}}feed" or root.tag.endswith("feed"):
        for e in root.iter(f"{{{_NS['atom']}}}entry"):
            link = next((l.get("href") for l in e.iter(f"{{{_NS['atom']}}}link") if l.get("rel") in (None, "alternate")), "")
            content = e.findtext(f"{{{_NS['atom']}}}content") or e.findtext(f"{{{_NS['atom']}}}summary") or ""
            items.append({
                "title": (e.findtext(f"{{{_NS['atom']}}}title") or "").strip(),
                "url": (link or "").split("?")[0].strip(),
                "html": content,
            })
    return items


def _slug(url: str, title: str) -> str:
    path = urlparse(url).path.strip("/").replace("/", "_")
    path = re.sub(r"\.(html|xml)$", "", path)
    return (path or re.sub(r"[^a-z0-9]+", "-", title.lower())[:80])[:120]


def sync(feed: str | None = None, backfill: bool = False) -> dict:
    names = [feed] if feed else list(FEEDS)
    manifest = load_manifest("rss")
    base = corpus_dir("rss")
    base.mkdir(parents=True, exist_ok=True)
    summary: dict = {}
    for name in names:
        url = FEEDS[name]
        known_urls = {k for k, v in manifest.items() if v.get("feed") == name}
        pages = [fetch_cached(url)]
        if backfill and name in BACKFILL_CAPABLE:
            sep = "&" if "?" in url else "?"
            for p in range(2, BACKFILL_MAX_PAGES + 1):
                fr_p = fetch_cached(f"{url}{sep}paged={p}")
                if fr_p.status != 200:
                    break
                try:
                    page_items = _parse_feed(fr_p.text)
                except ET.ParseError:
                    break
                page_urls = {it["url"] for it in page_items if it["url"]}
                new = page_urls - known_urls
                if not new:
                    break
                pages.append(fr_p)
                known_urls |= page_urls
        items = []
        for fr in pages:
            if fr.status != 200:
                summary[name] = {"error": fr.error or f"status {fr.status}"}
                continue
            try:
                items.extend(_parse_feed(fr.text))
            except ET.ParseError as e:
                summary[name] = {"error": f"parse: {e}"}
                continue
        if summary.get(name, {}).get("error"):
            continue
        changed = unchanged = thin = 0
        for it in items:
            post_url = it["url"]
            if not post_url:
                continue
            md = html_to_markdown(it["html"]).strip()
            rec = manifest.get(post_url, {})
            digest = sha256_text(md)
            if rec.get("sha256") == digest:
                unchanged += 1
                continue
            if len(md) < MIN_BODY:
                thin += 1
                manifest[post_url] = {**rec, "url": post_url, "source": "rss", "feed": name, "thin": True}
                continue
            body = base / f"{name}--{_slug(post_url, it['title'])}.md"
            body.write_text(f"# {it['title']}\n\n{md}\n")
            manifest[post_url] = {
                "url": post_url,
                "path": f"rss/{body.name}",
                "sha256": digest,
                "title": it["title"],
                "feed": name,
                "source": "rss",
            }
            changed += 1
        summary[name] = {"changed": changed, "unchanged": unchanged, "thin": thin, "items": len(items)}
    save_manifest("rss", manifest)
    return summary


if __name__ == "__main__":
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    backfill = "--backfill" in sys.argv
    print(json.dumps(sync(arg, backfill=backfill), indent=2))
