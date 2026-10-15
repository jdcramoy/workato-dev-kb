"""Static-HTML web source: engineering-book/essay corpora with no llms.txt, RSS,
or git markdown (scout wave 2, Josh 2026-10-11).

Generic per-site crawler in the eip_source idiom: start page(s) -> link-walk
(hops) or sitemap discovery -> fetch with ETag caching -> shared html_to_markdown
-> manifest rows keyed by page URL. One shared "webcrawl" manifest + corpus dir;
rows carry `site`. All content is internal-RAG-only (see README licensing notes).

    python3 -m kb.html_source            # all sites
    python3 -m kb.html_source sre-books  # one site
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from .common import corpus_dir, fetch_all, fetch_cached, load_manifest, save_manifest, sha256_text
from .wp_source import html_to_markdown

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_HREF_RE = re.compile(r'href="([^"#?]+)"', re.I)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)

MIN_MD = 400  # nav shells / stub pages produce less; skip them

# site -> config. `starts`: hub pages; `include`: full-URL filter for crawlable
# content pages; `exclude`: drop patterns; `hops`: walk depth from starts
# (1 = links on starts only); `max_pages` per site; `sitemap`: discover via
# /sitemap.xml instead of link-walking.
SITES = {
    # Josh 2026-10-13: the Builders Community Q&A — real-world troubleshooting
    # threads (server-side rendered, ~140KB/thread). Member profiles excluded
    # (PII); events/guidelines excluded (noise).
    "community-qa": {
        "starts": [
            "https://community.workato.com/x/questions",
            "https://community.workato.com/x/general",
            "https://community.workato.com/x/ideas-for-workato",
            "https://community.workato.com/x/build-out-loud",
        ],
        "include": re.compile(r"^https://community\.workato\.com/x/.+/msg_.+"),
        "exclude": re.compile(r"/members/|/events/|guidelines"),
        "hops": 2,
        "max_pages": 400,
    },
    "sre-books": {
        "starts": ["https://sre.google/books/"],
        "include": re.compile(r"^https://sre\.google/[^/]+/.*"),
        "exclude": re.compile(r"^https://sre\.google/(books|resources|thanks|about|community|media|faq)/?$|^https://sre\.google/?$"),
        "hops": 2,
        "max_pages": 450,
    },
    "mit-6824": {
        "starts": ["https://pdos.csail.mit.edu/6.824/schedule.html"],
        "include": re.compile(r"^https://pdos\.csail\.mit\.edu/6\.824/(notes|schedule|lectures)"),
        "exclude": re.compile(r"\.(pdf|mp4|pptx?|docx?)$"),
        "hops": 1,
        "max_pages": 80,
    },
    "use-the-index-luke": {
        "starts": ["https://use-the-index-luke.com/sql/table-of-contents"],
        "include": re.compile(r"^https://use-the-index-luke\.com/.+"),
        "exclude": re.compile(r"(imprint|/blog/feed|comment)"),
        "hops": 2,
        "max_pages": 300,
    },
    "jepsen": {
        "starts": ["https://jepsen.io/analyses"],
        "include": re.compile(r"^https://jepsen\.io/analyses/.+"),
        "exclude": re.compile(r"(feed|\.json)"),
        "hops": 1,
        "max_pages": 90,
    },
    "kimball": {
        "starts": [
            "https://www.kimballgroup.com/data-warehouse-business-intelligence-resources/kimball-techniques/",
            "https://www.kimballgroup.com/data-warehouse-business-intelligence-resources/kimball-techniques/dimensional-modeling-techniques/",
        ],
        "include": re.compile(r"^https://www\.kimballgroup\.com/.+"),
        "exclude": re.compile(r"(wp-login|feed|cart|checkout|shop|\.pdf)"),
        "hops": 2,
        "max_pages": 250,
    },
    "nielsen-nn": {
        "starts": ["http://neuralnetworksanddeeplearning.com/index.html"],
        "include": re.compile(r"^http://neuralnetworksanddeeplearning\.com/(index|chap\d+)\.html$"),
        "exclude": None,
        "hops": 1,
        "max_pages": 15,
    },
    "distill": {
        "starts": ["https://distill.pub/archive/"],
        "include": re.compile(r"^https://distill\.pub/[^/]+/?$"),
        "exclude": None,
        "hops": 1,
        "max_pages": 90,
    },
    "jalammar": {
        "starts": ["https://jalammar.github.io/"],
        "include": re.compile(r"^https://jalammar\.github\.io/.+"),
        "exclude": re.compile(r"(github\.com|twitter|\.png|\.svg|feed)"),
        "hops": 1,
        "max_pages": 60,
    },
    "brendan-gregg": {
        "starts": [
            "https://www.brendangregg.com/blog/index.html",
            "https://www.brendangregg.com/linuxpage.html",
            "https://www.brendangregg.com/methodology.html",
        ],
        "include": re.compile(r"^https://www\.brendangregg\.com/.+"),
        "exclude": re.compile(r"(blog/index|\.png|\.jpg|\.pdf|twitter|linkedin)"),
        "hops": 2,
        "max_pages": 300,
    },
    "microservices-io": {
        "starts": [
            "https://microservices.io/patterns/index.html",
            "https://microservices.io/patterns/microservices/index.html",
        ],
        "include": re.compile(r"^https://microservices\.io/(patterns|articles|refactoring)/.+\.html$"),
        "exclude": None,
        "hops": 2,
        "max_pages": 300,
    },
    "neuroscience-online": {
        "starts": ["https://nba.uth.tmc.edu/neuroscience/m/"],
        "include": re.compile(r"^https://nba\.uth\.tmc\.edu/neuroscience/m/s\d+/chapter\d+.*\.html?$"),
        "exclude": None,
        "hops": 2,
        "max_pages": 260,
    },
    "nature-of-code": {
        "starts": ["https://natureofcode.com/book/"],
        "include": re.compile(r"^https://natureofcode\.com/book/(intro|chapter-[^/]+|acknowledgments)/?(index\.html)?$"),
        "exclude": None,
        "hops": 1,
        "max_pages": 30,
    },
    "asknature": {
        "starts": ["https://asknature.org/"],
        "include": re.compile(r"^https://asknature\.org/(strategy|resource)/.+"),
        "exclude": None,
        "sitemap": True,
        "max_pages": 2200,
    },
    "numenta": {
        "starts": ["https://www.numenta.com/resources/htm/"],
        "include": re.compile(r"^https://www\.numenta\.com/(resources|m)/.+"),
        "exclude": re.compile(r"(\.pdf|\.png|arxiv|github\.com|youtube)"),
        "hops": 2,
        "max_pages": 60,
    },
    "felienne": {
        "starts": ["https://www.felienne.com/book"],
        "include": re.compile(r"^https://www\.felienne\.com/.+"),
        "exclude": re.compile(r"(\.pdf|twitter|github\.com|mastodon)"),
        "hops": 1,
        "max_pages": 30,
    },
    "fep-essays": {
        "starts": ["https://jaredtumiel.github.io/blog/"],
        "include": re.compile(r"^https://jaredtumiel\.github\.io/blog/.+"),
        "exclude": None,
        "hops": 1,
        "max_pages": 30,
    },
    # JS-rendered sites: crawled via their git sources instead of the live DOM.
    # distill: 53 per-article repos (post--*), article text in public/index.html,
    # default branch varies (master/main) — resolve via the GitHub API.
    "distill": {"distill_repos": True, "max_pages": 90},
    # Wave 3 (2026-10-11): Postgres manual SELECTIVE chapters (the full manual
    # is 4k pages of syntax reference; these five carry the design depth) +
    # AWS Well-Architected Framework (permitted-use license, internal RAG only).
    "postgres-core": {
        "starts": [
            "https://www.postgresql.org/docs/current/queries.html",
            "https://www.postgresql.org/docs/current/ddl.html",
            "https://www.postgresql.org/docs/current/indexes.html",
            "https://www.postgresql.org/docs/current/mvcc.html",
            "https://www.postgresql.org/docs/current/performance-tips.html",
        ],
        "include": re.compile(r"^https://www\.postgresql\.org/docs/current/[a-z0-9-]+\.html$"),
        "exclude": re.compile(r"(appendix|biblio|bookindex|\.svg|\.png)"),
        "hops": 1,
        "max_pages": 350,
    },
    "aws-waf": {
        "starts": ["https://docs.aws.amazon.com/wellarchitected/latest/framework/the-pillars-of-the-framework.html"],
        "include": re.compile(r"^https://docs\.aws\.amazon\.com/wellarchitected/latest/.+\.html$"),
        "exclude": re.compile(r"(\.pdf|\.png|\.svg)"),
        "hops": 2,
        "max_pages": 400,
    },
    # Wave 5 (2026-10-15, Josh): single-article/cluster pages — self-healing
    # systems, event-driven architecture, universality. `self_start: True`
    # ingests the starts themselves (the link-walk normally only ingests what
    # starts LINK to); `include` confines any hops to the same path subtree.
    "necsi-universality": {
        "starts": ["https://necsi.edu/14-universality"],
        "include": re.compile(r"^https://necsi\.edu/14-universality$"),
        "self_start": True,
        "hops": 1,
        "max_pages": 3,
    },
    "redhat-self-healing": {
        "starts": ["https://www.redhat.com/architect/portfolio/detail/17-self-healing-infrastructure"],
        "include": re.compile(r"^https://www\.redhat\.com/architect/portfolio/detail/17-self-healing-infrastructure$"),
        "self_start": True,
        "hops": 1,
        "max_pages": 3,
    },
    "azure-self-preservation": {
        "starts": ["https://learn.microsoft.com/en-us/azure/well-architected/reliability/self-preservation"],
        "include": re.compile(r"^https://learn\.microsoft\.com/en-us/azure/well-architected/reliability/[a-z0-9-]+$"),
        "exclude": None,
        "self_start": True,
        "hops": 1,
        "max_pages": 80,
    },
    "gfg-self-healing": {
        "starts": ["https://www.geeksforgeeks.org/system-design/self-healing-systems-system-design/"],
        "include": re.compile(r"^https://www\.geeksforgeeks\.org/system-design/self-healing-systems-system-design/?$"),
        "self_start": True,
        "hops": 1,
        "max_pages": 3,
    },
    "confluent-eda": {
        "starts": ["https://www.confluent.io/learn/event-driven-architecture/"],
        "include": re.compile(r"^https://www\.confluent\.io/learn/event-driven-architecture.*$"),
        "self_start": True,
        "hops": 1,
        "max_pages": 30,
    },
    "ibm-eda": {
        "starts": ["https://www.ibm.com/think/topics/event-driven-architecture"],
        "include": re.compile(r"^https://www\.ibm\.com/think/topics/[a-z0-9-]+$"),
        "self_start": True,
        "hops": 1,
        "max_pages": 60,
    },
    "medium-eda-patterns": {
        "starts": ["https://medium.com/@zcking/event-driven-design-patterns-for-data-engineering-ed6e9a6fafcc"],
        "include": re.compile(r"^https://medium\.com/@zcking/event-driven-design-patterns-for-data-engineering-ed6e9a6fafcc$"),
        "self_start": True,
        "hops": 1,
        "max_pages": 3,
    },
    "arxiv-2504-21048": {
        "starts": ["https://arxiv.org/html/2504.21048v1"],
        "include": re.compile(r"^https://arxiv\.org/html/2504\.21048v1$"),
        "self_start": True,
        "hops": 1,
        "max_pages": 3,
    },
}


def _page_title(html: str, url: str) -> str:
    m = _H1_RE.search(html) or _TITLE_RE.search(html)
    if not m:
        return url.rstrip("/").rsplit("/", 1)[-1]
    t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return t.split("|")[0].split(" - ")[0].split(" — ")[0].strip()[:150]


def _links(html: str, base: str) -> set[str]:
    out = set()
    base_dir = base.rsplit("/", 1)[0]
    origin = "/".join(base.split("/")[:3])
    for href in _HREF_RE.findall(html):
        href = href.strip()
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = origin + href
        elif href.startswith("http"):
            pass
        else:  # site-relative (chap1.html, ../foo/bar.html)
            href = f"{base_dir}/{href}"
        if href.startswith("http"):
            out.add(href.split("#")[0].rstrip("/"))
    return out


def _collect(site: str, cfg: dict) -> dict[str, str]:
    """Discover content URLs -> {page_url: fetch_url} (they differ for git-sourced sites)."""
    if cfg.get("distill_repos"):
        return _collect_distill(cfg["max_pages"])
    include, exclude = cfg["include"], cfg.get("exclude")
    ok = lambda u: bool(include.match(u)) and not (exclude and exclude.search(u))  # noqa: E731
    if cfg.get("sitemap"):
        origin = "/".join(cfg["starts"][0].split("/")[:3])
        urls: set[str] = set()
        queue = [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml", f"{origin}/wp-sitemap.xml"]
        seen_maps = set()
        while queue and len(urls) < cfg["max_pages"] * 3:
            sm = queue.pop(0)
            if sm in seen_maps:
                continue
            seen_maps.add(sm)
            fr = fetch_cached(sm)
            if fr.status != 200:
                continue
            locs = _LOC_RE.findall(fr.text)
            for loc in locs:
                if loc.endswith(".xml") or loc.endswith(".xml.gz"):
                    queue.append(loc)
                elif ok(loc):
                    urls.add(loc)
        return {u: u for u in sorted(urls)[: cfg["max_pages"]]}
    urls: set[str] = set()
    frontier = list(cfg["starts"])
    seen_pages: set[str] = set()
    if cfg.get("self_start"):
        # single-article sites: the starts ARE the content (the link-walk
        # below only ingests what they link to)
        urls.update(u for u in frontier if ok(u))
    for hop in range(cfg["hops"]):
        nxt: set[str] = set()
        jobs = [(u, "") for u in frontier if u not in seen_pages]
        results = fetch_all(jobs) if len(jobs) > 4 else [fetch_cached(u) for u, _ in jobs]
        for (u, _), fr in zip(jobs, results):
            seen_pages.add(u)
            if fr.status != 200:
                continue
            for link in _links(fr.text, u):
                if ok(link) and link not in urls:
                    urls.add(link)
                    if hop + 1 < cfg["hops"]:
                        nxt.add(link)
        frontier = sorted(nxt)
    return {u: u for u in sorted(urls)[: cfg["max_pages"]]}
def _collect_distill(max_pages: int) -> dict[str, str]:
    """distill.pub -> {page_url: public/index.html raw URL} via the distillpub
    org's post--* repos (article HTML lives in the repo, not on the JS-rendered
    site). Default branch resolved per repo (master for old, main for new).
    """
    import urllib.request

    api = "https://api.github.com/orgs/distillpub/repos?per_page=100"
    req = urllib.request.Request(api, headers={"User-Agent": "kb-pipeline"})
    with urllib.request.urlopen(req, timeout=30) as r:
        repos = json.load(r)
    out = {}
    for repo in repos:
        name = repo.get("name", "")
        if not name.startswith("post--"):
            continue  # template/drafts and non-article repos
        branch = repo.get("default_branch") or "main"
        slug = name.removeprefix("post--")
        out[f"https://distill.pub/{slug}"] = (
            f"https://raw.githubusercontent.com/distillpub/{name}/{branch}/public/index.html"
        )
    return dict(list(out.items())[:max_pages])


def sync(site: str | None = None) -> dict:
    names = [site] if site else list(SITES)
    manifest = load_manifest("webcrawl")
    base = corpus_dir("webcrawl")
    base.mkdir(parents=True, exist_ok=True)
    summary: dict = {}
    for name in names:
        cfg = SITES[name]
        fetch_map = _collect(name, cfg)  # page_url -> fetch_url (same except git-sourced sites)
        # dedupe: multiple page slugs can share a fetch_url (redirect variants)
        seen_fetch: dict[str, str] = {}
        deduped = []
        for page_url, fetch_url in fetch_map.items():
            if fetch_url in seen_fetch:
                continue
            seen_fetch[fetch_url] = page_url
            deduped.append((page_url, fetch_url))
        urls = [p for p, _ in deduped]
        jobs = [(f_url, manifest.get(p_url, {}).get("etag", "")) for p_url, f_url in deduped]
        results = fetch_all(jobs, progress_every=200)
        changed = unchanged = thin = skipped = 0
        for (u, _f_url), fr in zip(deduped, results):
            if fr.status == 304:
                unchanged += 1
                continue
            if fr.status != 200:
                skipped += 1
                continue
            # only re-convert when content actually changed (cheap digest on html)
            digest = sha256_text(fr.text)
            rec = manifest.get(u, {})
            if rec.get("sha256") == digest:
                unchanged += 1
                continue
            path_seg = urlparse(u).path.strip("/").replace("/", "_")
            path_seg = re.sub(r"\.html?$", "", path_seg) or "index"
            md = html_to_markdown(fr.text).strip()
            if len(md) < MIN_MD:
                thin += 1
                continue
            title = _page_title(fr.text, u)
            body = base / f"{name}--{path_seg[:110]}.md"
            body.write_text(f"# {title}\n\nSource: {u}\n\n{md}\n")
            manifest[u] = {
                "url": u,
                "path": f"webcrawl/{body.name}",
                "sha256": digest,
                "etag": fr.etag,
                "title": title,
                "site": name,
                "source": "webcrawl",
            }
            changed += 1
        summary[name] = {"discovered": len(urls), "changed": changed, "unchanged": unchanged, "thin": thin, "skipped": skipped}
    save_manifest("webcrawl", manifest)
    return summary


if __name__ == "__main__":
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(sync(arg), indent=2))
