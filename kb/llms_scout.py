"""llms.txt directory scout: crawl the public llms.txt directories into one queue.

Josh mandate ("crawl all of this and its linked documentation... set up pipelines
to keep doing it"): the two public directories of llms.txt files are harvested into
a single append-only queue file that feeds FUTURE ingest waves. Auto-ingest is
explicitly OUT of scope — the queue is the deliverable; Josh or the librarian picks
sites from it.

Sources:
  - SecretiveShell/Awesome-llms-txt: json/llms-txt.json (~630 llms.txt URLs) and
    json/urls.json (superset: llms.txt + 163 llms-full.txt twins).
  - thedaviddias/llms-txt-hub: packages/content/data/{websites,guides,resources,
    unofficial}/*.mdx — YAML-frontmatter cards (name/description/llmsUrl/category).

    python -m kb.llms_scout   # clone-or-pull both repos, rebuild the queue, write report
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .common import DATA_ROOT

SCOUT_DIR = DATA_ROOT / "scout"
REPOS_DIR = SCOUT_DIR / "repos"
QUEUE_PATH = SCOUT_DIR / "llms-site-queue.jsonl"
STATE_PATH = SCOUT_DIR / "llms-scout-state.json"

REPOS = {
    # name -> (clone URL, default branch)
    "awesome-llms-txt": ("https://github.com/SecretiveShell/Awesome-llms-txt", "master"),
    "llms-txt-hub": ("https://github.com/thedaviddias/llms-txt-hub", "main"),
}

# Directory hygiene: the directories genuinely contain spam (abonnement-iptv-france.fr
# et al). Rather than silently dropping, rejected rows are COUNTED and sampled in the
# report — the ledger stays honest without writing hundreds of junk rows to the queue.
BAD_SUBSTRINGS = ("iptv", "casino", "porn", "escort", "betting", "viagra")

# Hub dirs walked for .mdx cards (unofficial ships hub-hosted llms.txt files with
# RELATIVE llmsUrl paths — not external sites, skipped and counted).
HUB_DATA_DIRS = ("websites", "guides", "resources", "unofficial")


def _today() -> str:
    return dt.date.today().isoformat()


# --- repo fetch -------------------------------------------------------------

def _git(*args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=300)
    return r.returncode, (r.stdout + r.stderr).strip()


def ensure_repo(name: str) -> dict:
    """Clone --depth 1 once, then git pull --ff-only to refresh. Per-repo tolerance:
    a failed fetch must not kill the run — the stale checkout still parses."""
    url, branch = REPOS[name]
    path = REPOS_DIR / name
    if (path / ".git").is_dir():
        code, out = _git("-C", str(path), "pull", "--ff-only")
        return {"path": str(path), "status": "ok" if code == 0 else "stale", "detail": out.splitlines()[-1] if out else ""}
    path.parent.mkdir(parents=True, exist_ok=True)
    code, out = _git("clone", "--depth", "1", "--branch", branch, url, str(path))
    return {"path": str(path), "status": "ok" if code == 0 else "error", "detail": out.splitlines()[-1] if out else ""}


# --- parsers ----------------------------------------------------------------

def parse_awesome(repo_dir: Path) -> tuple[list[dict], dict]:
    """-> (candidates, stats). urls.json is a superset (llms.txt + llms-full.txt
    twins); an llms-full.txt URL only becomes a candidate when its site has no
    plain llms.txt entry (otherwise it's a redundant single-file dump)."""
    stats = {"llms_txt_entries": 0, "urls_json_entries": 0, "llms_full_dropped": 0}
    llms_txt = json.loads((repo_dir / "json/llms-txt.json").read_text())
    urls = json.loads((repo_dir / "json/urls.json").read_text())
    stats["llms_txt_entries"] = len(llms_txt)
    stats["urls_json_entries"] = len(urls)
    with_plain = {u.rsplit("/", 1)[0] for u in llms_txt if isinstance(u, str)}
    candidates: dict[str, dict] = {}
    for u in urls:
        if not isinstance(u, str) or not u.startswith("http"):
            continue
        if u.endswith("llms-full.txt"):
            if u.rsplit("/", 1)[0] in with_plain:
                stats["llms_full_dropped"] += 1
                continue  # site already represented by its llms.txt index
        candidates[u.rstrip("/")] = {
            "url": u, "title": "", "description": "", "category": "", "sources": ["awesome-llms-txt"],
        }
    return list(candidates.values()), stats


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _frontmatter(text: str) -> str:
    """Body between the first `---` line and the closing `---` line, else ''.
    Stray control characters (one hub file ships a literal 0x80) are stripped
    before YAML parsing rather than losing the whole card."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return _CTRL.sub("", text[4:end]) if end != -1 else ""


def parse_hub(repo_dir: Path) -> tuple[list[dict], dict]:
    """-> (candidates, stats). Each mdx is a YAML-frontmatter card. Only ABSOLUTE
    http llmsUrl/llmsFullUrl entries queue — relative paths are hub-hosted files,
    not external llms.txt directories."""
    import yaml  # PyYAML ships in the venv

    stats = {"mdx_files": 0, "skipped_relative": 0, "parse_errors": 0}
    candidates: dict[str, dict] = {}
    data_root = repo_dir / "packages/content/data"
    for sub in HUB_DATA_DIRS:
        d = data_root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.mdx")):
            stats["mdx_files"] += 1
            try:
                meta = yaml.safe_load(_frontmatter(p.read_text(errors="replace"))) or {}
            except Exception:  # noqa: BLE001 - one bad mdx must not kill the sweep
                stats["parse_errors"] += 1
                continue
            if not isinstance(meta, dict):
                stats["parse_errors"] += 1
                continue
            llms_url = (meta.get("llmsUrl") or "").strip()
            if not llms_url.startswith("http"):
                full = (meta.get("llmsFullUrl") or "").strip()
                if full.startswith("http"):
                    llms_url = full
                else:
                    stats["skipped_relative"] += 1
                    continue
            candidates[llms_url.rstrip("/")] = {
                "url": llms_url,
                "title": str(meta.get("name") or ""),
                "description": str(meta.get("description") or ""),
                "category": str(meta.get("category") or ""),
                "sources": ["llms-txt-hub"],
            }
    return list(candidates.values()), stats


# --- hygiene + dedupe -------------------------------------------------------

def _hygiene_reject(url: str) -> str | None:
    """Return a reject reason, or None if the URL passes basic hygiene."""
    host = (urlparse(url).netloc or "").lower()
    for bad in BAD_SUBSTRINGS:
        if bad in host:
            return f"hygiene: domain contains {bad!r}"
    return None


def _netloc(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def _covered_origins() -> set[str]:
    """Origins (netlocs, www-stripped) already ingested by kb.llms_source."""
    from . import llms_source

    out = set()
    for u in list(llms_source.SITES.values()) + list(llms_source.SITEMAPS.values()):
        out.add(_netloc(u))
    return out


# --- queue merge ------------------------------------------------------------

def load_queue() -> dict[str, dict]:
    """url -> row. Corrupt rows are skipped, not fatal (ledger semantics)."""
    rows: dict[str, dict] = {}
    if not QUEUE_PATH.exists():
        return rows
    for line in QUEUE_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("url"):
            rows[rec["url"]] = rec
    return rows


def merge_queue(existing: dict[str, dict], candidates: dict[str, dict], covered: set[str]) -> dict:
    """Upsert on url. first_seen is NEVER rewritten; last_seen refreshes on sight;
    sources union; rejected rows keep their status; ingested-origin is re-evaluated
    each run (a site added to SITES later upgrades queued -> ingested)."""
    today = _today()
    changed = 0
    added: list[str] = []
    for url, cand in sorted(candidates.items()):
        prev = existing.get(url)
        if prev:
            row = prev
            row["last_seen"] = today
            row["sources"] = sorted(set(row.get("sources", [])) | set(cand["sources"]))
            if not row.get("title") and cand["title"]:
                row["title"] = cand["title"]
            if not row.get("description") and cand["description"]:
                row["description"] = cand["description"]
            if not row.get("category") and cand.get("category"):
                row["category"] = cand["category"]
            if row.get("status") != "rejected":
                row["status"] = "ingested" if _netloc(url) in covered else "queued"
        else:
            changed += 1
            added.append(url)
            origin = urlparse(url)
            site = f"{origin.scheme}://{origin.netloc}"
            status = "ingested" if _netloc(url) in covered else "queued"
            row = {
                "url": url,
                "site": site,
                "title": cand["title"],
                "description": cand["description"],
                "category": cand.get("category", ""),
                "sources": cand["sources"],
                "status": status,
                "first_seen": today,
                "last_seen": today,
            }
            existing[url] = row
    return {"changed": changed, "added": added}


def save_queue(rows: dict[str, dict]) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_PATH.open("w") as f:
        for url in sorted(rows):
            f.write(json.dumps(rows[url], sort_keys=True) + "\n")


# --- report -----------------------------------------------------------------

def write_report(today: str, summary: dict, new_sites: list[str], rejects: list[dict],
                 per_source: dict, awesome_stats: dict, hub_stats: dict) -> Path:
    p = SCOUT_DIR / f"llms-scout-report-{today}.md"
    lines = [
        f"# llms.txt scout report — {today}",
        "",
        "## Verdict",
        "",
        f"Queue holds **{summary['queued']} queued + {summary['ingested']} ingested** candidate sites "
        f"({summary['rejected']} rejected as spam). "
        f"{'New this run: ' + str(summary['changed']) + '.' if summary['changed'] else 'No new sites this run.'}",
        "",
        "## Counts per source",
        "",
    ]
    for src, st in sorted(per_source.items()):
        lines.append(f"- {src}: {st} candidates")
    lines += [
        "",
        f"- Cross-source overlaps (both repos list the site): {sum(1 for r in load_queue().values() if len(r.get('sources', [])) > 1)}",
        "",
        "## Repos",
        "",
    ]
    for name, st in summary["repos"].items():
        lines.append(f"- {name}: {st.get('status')} ({st.get('detail', '')[:80]})")
    lines += [
        "",
        "## Parse details",
        "",
        f"- awesome-llms-txt: llms-txt.json {awesome_stats.get('llms_txt_entries', 0)} entries, "
        f"urls.json {awesome_stats.get('urls_json_entries', 0)} entries, "
        f"{awesome_stats.get('llms_full_dropped', 0)} llms-full.txt twins dropped (site has an llms.txt index)",
        f"- llms-txt-hub: {hub_stats.get('mdx_files', 0)} mdx files, "
        f"{hub_stats.get('skipped_relative', 0)} skipped (hub-hosted relative llmsUrl), "
        f"{hub_stats.get('parse_errors', 0)} parse errors",
        "",
        "## Dedupe vs kb.llms_source SITES/SITEMAPS",
        "",
        f"- {summary['ingested']} origins in the queue are already ingested sources "
        "(status ingested, not queued).",
        "",
        "## Newly discovered this run",
        "",
        f"- {summary['changed']} new URL(s) added to the queue.",
    ]
    if new_sites:
        lines += [""] + [f"- {u}" for u in new_sites[:25]]
    lines += ["", "## Rejected (hygiene)", ""]
    if rejects:
        lines.append(f"- {len(rejects)} rejected URL(s); examples:")
        lines += [f"  - {r['url']} ({r['reason']})" for r in rejects[:10]]
    else:
        lines.append("- none")
    p.write_text("\n".join(lines) + "\n")
    return p


# --- main -------------------------------------------------------------------

def sync() -> dict:
    today = _today()
    repos = {name: ensure_repo(name) for name in REPOS}

    awesome_cands, awesome_stats = (parse_awesome(REPOS_DIR / "awesome-llms-txt")
                                    if (REPOS_DIR / "awesome-llms-txt" / ".git").is_dir() else ([], {"skipped": 1}))
    hub_cands, hub_stats = (parse_hub(REPOS_DIR / "llms-txt-hub")
                            if (REPOS_DIR / "llms-txt-hub" / ".git").is_dir() else ([], {"skipped": 1}))

    # cross-source merge: same URL listed by both repos -> sources union
    candidates: dict[str, dict] = {}
    for cand in awesome_cands + hub_cands:
        key = cand["url"].rstrip("/")
        if key in candidates:
            candidates[key]["sources"] = sorted(set(candidates[key]["sources"]) | set(cand["sources"]))
            for f in ("title", "description", "category"):
                if not candidates[key][f] and cand[f]:
                    candidates[key][f] = cand[f]
        else:
            candidates[key] = cand

    # hygiene first: rejects are counted and sampled, never written to the queue
    rejects: list[dict] = []
    for key, cand in sorted(candidates.items()):
        reason = _hygiene_reject(cand["url"])
        if reason:
            rejects.append({"url": cand["url"], "reason": reason})
    for r in rejects:
        candidates.pop(r["url"].rstrip("/"), None)

    covered = _covered_origins()
    existing = load_queue()
    result = merge_queue(existing, candidates, covered)
    save_queue(existing)

    rows = list(existing.values())
    queued = sum(1 for r in rows if r.get("status") == "queued")
    ingested = sum(1 for r in rows if r.get("status") == "ingested")
    new_sites = result["added"]

    per_source: dict[str, int] = {}
    for cand in candidates.values():
        for s in cand["sources"]:
            per_source[s] = per_source.get(s, 0) + 1

    summary = {
        "changed": result["changed"],
        "queued": queued,
        "ingested": ingested,
        "rejected": len(rejects),
        "repos": repos,
    }

    report_path = write_report(today, summary, new_sites, rejects, per_source,
                               awesome_stats, hub_stats)

    state = {
        "last_run": dt.datetime.now().isoformat(timespec="seconds"),
        "last_summary": summary,
        "report": str(report_path),
        # rejected rows live only here (count + sample): the queue file carries
        # queued+ingested rows only, so the ledger stays honest without junk rows
        "rejected_count": len(rejects),
        "rejected_examples": rejects[:10],
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))

    print(f"[llms_scout] {summary}", flush=True)
    return summary


if __name__ == "__main__":
    import sys

    print(json.dumps(sync(), indent=2))
    sys.exit(0)
