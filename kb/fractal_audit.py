"""Invariant auditor for the fractal memory store (fractal.db).

Unlike the older file-coverage auditor (which only checked that level-1 files
were ingested), this audits RUNG completeness of the fractal hierarchy:

  - level 0 = topic, level 1 = file, level 2 = section, level 3 = chunk

Bars (verdict line, exit 1 on FAIL so a scheduled job can alert):

  - sections : >= 80% of level-1 files have >= 1 level-2 child
  - chunks   : >= 95% of level-1 files have >= 1 level-3 child
  - topics   : >= 20 level-0 factlets and 0 stubs (text < 40 chars)
  - orphans  : 0 factlets whose parent id does not resolve

Usage:
  python3 -m kb.fractal_audit            # pretty table against the real db
  python3 -m kb.fractal_audit --json     # machine-readable report dict
  python3 -m kb.fractal_audit --db PATH  # audit a different db (self-test)
  python3 -m kb.fractal_audit --selftest # synthetic-db proof each check trips

Read-only: opens the db read-only (mode=ro) and never writes.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

DEFAULT_DB = os.path.expanduser("~/.claude/workato-kb/index/fractal.db")

LEVEL_NAMES = {0: "topic", 1: "file", 2: "section", 3: "chunk"}

# ---- BARS (single source of truth for the verdict) -------------------------
BAR_SECTIONS_PCT = 80.0   # % of level-1 files with >=1 level-2 child
BAR_CHUNKS_PCT = 95.0     # % of level-1 files with >=1 level-3 child
BAR_MIN_TOPICS = 20       # minimum level-0 factlets
BAR_MAX_TOPIC_STUBS = 0   # maximum level-0 stubs (text < 40 chars)
BAR_MAX_ORPHANS = 0       # maximum unresolvable parent links
STUB_TEXT_LEN = 40        # text shorter than this = stub
MIN_TOPIC_CHILDREN = 3    # health metric: every level-0 should have >=3 children


def open_ro(db_path):
    """Open a sqlite db strictly read-only so the audit can never mutate it."""
    uri = "file:{}?mode=ro".format(Path(db_path).resolve().as_posix())
    return sqlite3.connect(uri, uri=True)


def load_factlets(conn):
    """All factlets as (id, level, text_len, parent, source) tuples."""
    cur = conn.execute(
        "SELECT id, level, length(text), parent, source FROM factlets"
    )
    return cur.fetchall()


def _kb_url2path():
    """docs.url -> docs.path from kb.db (read-only); {} when unavailable.

    Normalizes the two level-1 source conventions (relative path from the
    kb-chunks layer vs raw doc URL from the section-pass heal) onto one file
    identity for the section/chunk coverage denominators.
    """
    try:
        kb_db = Path.home() / ".claude/workato-kb/index/kb.db"
        c = sqlite3.connect(f"file:{kb_db}?mode=ro", uri=True, timeout=30)
        m = {u: p for u, p in c.execute("SELECT url, path FROM docs WHERE url IS NOT NULL AND path IS NOT NULL")}
        c.close()
        return m
    except Exception:  # noqa: BLE001 - no kb.db: convention fix degrades to no-op
        return {}


def _flat_headingless_docs(paths, url2path):
    """Of `paths` (corpus relative paths), the set whose kb.db docs have ZERO
    ##/###-heading-initial chunks — the by-design flat docs (no section rung
    is ever built for them). Read-only; returns {} when kb.db is unavailable.
    """
    if not paths:
        return set()
    url2p = {v: k for k, v in url2path.items()}
    wanted_urls = {url2p[p] for p in paths if p in url2p}
    if not wanted_urls:
        return set()
    try:
        kb_db = Path.home() / ".claude/workato-kb/index/kb.db"
        c = sqlite3.connect(f"file:{kb_db}?mode=ro", uri=True, timeout=30)
        c.execute("CREATE TEMP TABLE wanted(url TEXT PRIMARY KEY)")
        c.executemany("INSERT OR IGNORE INTO wanted VALUES (?)", [(u,) for u in wanted_urls])
        heading_urls = set()
        for url, text in c.execute(
            "SELECT d.url, c.text FROM chunks c JOIN docs d ON d.doc_id=c.doc_id "
            "JOIN wanted w ON w.url=d.url"
        ):
            first = (text or "").lstrip().split("\n", 1)[0]
            if re.match(r"^#{2,3}\s", first):
                heading_urls.add(url)
        c.close()
        flat_urls = wanted_urls - heading_urls
        return {p for p in paths if url2p.get(p, p) in flat_urls}
    except Exception:  # noqa: BLE001 - degrade to conservative (no exclusions)
        return set()


def _kb_chunkable_urls():
    """Corpus URLs that actually have chunks in kb.db (read-only).

    Returns None when kb.db is missing/unreadable: the chunk bar then measures
    every corpus doc (conservative).
    """
    try:
        kb_db = Path.home() / ".claude/workato-kb/index/kb.db"
        c = sqlite3.connect(f"file:{kb_db}?mode=ro", uri=True, timeout=30)
        urls = {r[0] for r in c.execute(
            "SELECT DISTINCT d.url FROM docs d "
            "WHERE d.doc_id IN (SELECT doc_id FROM chunks) AND d.url IS NOT NULL"
        )}
        c.close()
        return urls
    except Exception as e:  # noqa: BLE001 - no kb.db: fall back to conservative bar
        print(f"[kb.db unavailable ({e}); chunk bar includes unchunkable docs]",
              file=sys.stderr)
        return None


def audit(db_path, kb_urls=None):
    """Compute the full report dict from a factlet db. Read-only."""
    conn = open_ro(db_path)
    try:
        rows = load_factlets(conn)
    finally:
        conn.close()

    ids = {r[0] for r in rows}
    by_level = {}
    for fid, level, tlen, parent, source in rows:
        by_level.setdefault(level, []).append((fid, tlen, parent, source))

    # -- per-level counts ----------------------------------------------------
    levels = {}
    for lv in sorted(set(list(by_level.keys()) + [0, 1, 2, 3])):
        recs = by_level.get(lv, [])
        levels[lv] = {
            "name": LEVEL_NAMES.get(lv, "unknown"),
            "count": len(recs),
            "distinct_sources": len({r[3] for r in recs if r[3]}),
        }

    # -- children via parent links -------------------------------------------
    children_by_parent = {}
    for fid, level, tlen, parent, source in rows:
        if parent:
            children_by_parent.setdefault(parent, []).append((fid, level))

    # File linkage: a level-1 "file" is identified by its source path. A
    # level-2/3 factlet is a child of that file either when its parent chain
    # reaches the file's id (strict parent links) or when it carries the same
    # source path (the linkage convention used at ingestion time). Union of
    # both, so the audit is robust to which convention the repair lands on.
    # Two level-1 source conventions exist for corpus docs: a relative path
    # (kb-chunks layer) and the doc URL (section-pass heal). Map URL-source
    # factlets onto the doc's relative path via kb.db so both conventions
    # credit the SAME file identity — otherwise the heal-emitted half counts
    # as an uncreditable section-less file (the phantom 2.5k of 2026-10-11).
    url2path = _kb_url2path()
    def norm_src(s):
        if s and str(s).startswith("http"):
            return url2path.get(s, s)
        return s
    file_sources = {norm_src(r[3]) for r in by_level.get(1, []) if r[3]}

    # ancestors for level-2/3: resolve each row's parent chain to a level-1 id
    parent_of = {r[0]: r[3] for r in rows}

    def ancestor_file_level(fid):
        """Walk parent chain up; return True if it reaches a level-1 factlet."""
        seen = set()
        cur = parent_of.get(fid)
        while cur and cur not in seen:
            seen.add(cur)
            if cur in level1_ids:
                return True
            cur = parent_of.get(cur)
        return False

    level1_ids = {r[0] for r in by_level.get(1, [])}
    level1_source = {r[0]: r[3] for r in by_level.get(1, [])}
    sec_via_link = set()
    chunk_via_link = set()
    for fid, level, tlen, parent, source in rows:
        if level == 2 and source:
            sec_via_link.add(source)
        if level == 3 and source:
            chunk_via_link.add(source)
    # corpus factlets carry URLs (or unrelated strings) as source: the real
    # linkage to the file is the PARENT chain. Resolve each level-2/3 factlet
    # up to its level-1 ancestor and credit that file's source path.
    for fid, level, tlen, parent, source in rows:
        if level not in (2, 3):
            continue
        seen = set()
        cur = parent_of.get(fid)
        found = None
        while cur and cur not in seen:
            seen.add(cur)
            if cur in level1_ids:
                found = cur
                break
            cur = parent_of.get(cur)
        if found is not None:
            src1 = norm_src(level1_source.get(found))
            if src1:
                (sec_via_link if level == 2 else chunk_via_link).add(src1)

    n_files_all = len(file_sources)
    # source-path conventions: corpus factlets carry RELATIVE paths (docs/en/...,
    # hubs/hub-x.md, eip/...). Hub files
    # are synthetic cluster indexes, deliberately section/chunk-free: excluded
    # from the section/chunk denominators.
    is_hub = lambda s: s.startswith("hubs/") or s.startswith("hubs://")  # noqa: E731
    file_sources_sections = {s for s in file_sources if not is_hub(s)}
    # chunk rung applies to: every corpus doc (its level-1 factlet is only a
    # summary; chunks carry the content), plus oversized local files truncated
    # at the 8000-char factlet cap (chunks carry the rest). Small files are
    # fully contained in their file factlet and need no chunk children.
    needs_chunks = set()
    for fid, level, tlen, parent, source in rows:
        if level != 1 or not source:
            continue
        if source.startswith("hubs/") or source.startswith("hubs://"):
            continue  # synthetic cluster indexes: no chunk rung by design
        if source.startswith("http"):
            needs_chunks.add(source)  # corpus doc: its level-1 factlet is a summary
        elif (tlen or 0) >= 8000:
            needs_chunks.add(source)  # oversized local file truncated at the factlet cap
    # corpus docs with ZERO chunks in kb.db have no chunkable content (empty
    # bodies, press releases, stub pages): their missing chunk rung is a fact
    # about the source, not a defect — exclude from the denominator
    chunkable = kb_urls if kb_urls is not None else _kb_chunkable_urls()
    if chunkable is not None:
        needs_chunks = {s for s in needs_chunks if not s.startswith("http")} | (
            {s for s in needs_chunks if s.startswith("http")} & chunkable
        )
    file_sources_chunks = needs_chunks

    n_files = len(file_sources_sections)
    files_with_sections = len(file_sources_sections & sec_via_link)
    files_with_chunks = len(file_sources_chunks & chunk_via_link)
    # Flat-by-design docs: the projection deliberately hangs chunks straight off
    # the file rung for docs with NO ##/### headings (essay/blog prose; see the
    # kbsec block in fractal.py — "nothing to section here"). Those are a fact
    # about the source's shape, not a broken section rung: exclude them from
    # the sections denominator (same spirit as the hubs exclusion).
    flat_docs = _flat_headingless_docs(file_sources_sections - sec_via_link, url2path)
    n_files_sections_gate = n_files - len(flat_docs)
    sections_pct = (100.0 * files_with_sections / n_files_sections_gate) if n_files_sections_gate else 0.0
    n_chunkable = len(file_sources_chunks)
    chunks_pct = (100.0 * files_with_chunks / n_chunkable) if n_chunkable else 0.0

    # -- topic health ----------------------------------------------------------
    topics = by_level.get(0, [])
    n_topics = len(topics)
    stubs = [t[0] for t in topics if (t[1] or 0) < STUB_TEXT_LEN]
    topics_with_kids = sum(
        1 for t in topics
        if sum(1 for c in children_by_parent.get(t[0], []) if c[1] == 1)
        >= MIN_TOPIC_CHILDREN
    )
    topics_kids_pct = (100.0 * topics_with_kids / n_topics) if n_topics else 0.0

    # -- orphans ---------------------------------------------------------------
    orphans = [r[0] for r in rows if r[3] and r[3] not in ids]

    # -- verdicts ---------------------------------------------------------------
    verdicts = {
        "sections": {
            "bar": ">= {:.0f}% of non-flat files with >=1 section".format(BAR_SECTIONS_PCT),
            "observed": round(sections_pct, 2),
            "flat_excluded": len(flat_docs),
            "pass": sections_pct >= BAR_SECTIONS_PCT,
        },
        "chunks": {
            "bar": ">= {:.0f}% of files with >=1 chunk".format(BAR_CHUNKS_PCT),
            "observed": round(chunks_pct, 2),
            "pass": chunks_pct >= BAR_CHUNKS_PCT,
        },
        "topics": {
            "bar": ">= {} topics and <= {} stubs".format(
                BAR_MIN_TOPICS, BAR_MAX_TOPIC_STUBS),
            "observed": {"count": n_topics, "stubs": len(stubs),
                         "stub_ids": stubs[:20]},
            "pass": n_topics >= BAR_MIN_TOPICS
            and len(stubs) <= BAR_MAX_TOPIC_STUBS,
        },
        "orphans": {
            "bar": "== {} unresolvable parents".format(BAR_MAX_ORPHANS),
            "observed": {"count": len(orphans), "ids": orphans[:20]},
            "pass": len(orphans) <= BAR_MAX_ORPHANS,
        },
    }
    verdicts["overall"] = {
        "pass": all(v["pass"] for k, v in verdicts.items() if k != "overall"),
    }

    return {
        "db": str(db_path),
        "levels": levels,
        "files": {
            "count": n_files,
            "with_sections": files_with_sections,
            "with_chunks": files_with_chunks,
            "sections_pct": round(sections_pct, 2),
            "chunks_pct": round(chunks_pct, 2),
        },
        "topic_health": {
            "count": n_topics,
            "stubs": len(stubs),
            "pct_with_min_children": round(topics_kids_pct, 2),
        },
        "orphans": len(orphans),
        "verdicts": verdicts,
    }


def render(report):
    lines = []
    lines.append("fractal invariant audit — {}".format(report["db"]))
    lines.append("-" * 72)
    lines.append("{:<8} {:>8} {:>14}".format("level", "count", "distinct src"))
    for lv in sorted(report["levels"]):
        e = report["levels"][lv]
        lines.append("{:<8} {:>8} {:>14}".format(
            e["name"], e["count"], e["distinct_sources"]))
    lines.append("-" * 72)
    f = report["files"]
    lines.append("file coverage per rung ({} level-1 files):".format(f["count"]))
    lines.append("  sections: {}/{} files have >=1 level-2 child ({}%)".format(
        f["with_sections"], f["count"], f["sections_pct"]))
    lines.append("  chunks:   {}/{} files have >=1 level-3 child ({}%)".format(
        f["with_chunks"], f["count"], f["chunks_pct"]))
    th = report["topic_health"]
    lines.append("topic health: {} topics, {} stubs (<{} chars), "
                 "{}% with >={}-3 children".format(
                     th["count"], th["stubs"], STUB_TEXT_LEN,
                     th["pct_with_min_children"], MIN_TOPIC_CHILDREN))
    lines.append("orphans (unresolvable parent): {}".format(report["orphans"]))
    lines.append("-" * 72)
    for key in ("sections", "chunks", "topics", "orphans"):
        v = report["verdicts"][key]
        lines.append("  {:<9} {}  ({}; observed: {})".format(
            key.upper(), "PASS" if v["pass"] else "FAIL", v["bar"],
            v["observed"]))
    lines.append("  OVERALL   {}".format(
        "PASS" if report["verdicts"]["overall"]["pass"] else "FAIL"))
    return "\n".join(lines)


# ---- synthetic self-test ----------------------------------------------------

def _mkdb(path):
    """Fabricate a tiny fractal db that trips every check on purpose.

    - topic T_full: healthy (long text, 3 file children)
    - topic T_stub: stub (short text)
    - file F_good (source 'good.md'): 2 sections + 2 chunks, parent T_full
    - file F_bad (source 'bad.md'): 1 chunk only, NO sections, parent T_full
    - file F_also (source 'also.md'): 1 chunk only, parent T_full
      (T_full then has exactly 3 file children -> passes the >=3-children
      metric, so that check is exercised, not vacuously 0%)
    - row X_orphan: parent 'no-such-id' (orphan)
    """
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE factlets (id TEXT PRIMARY KEY, level INTEGER NOT NULL, "
        "title TEXT NOT NULL, text TEXT NOT NULL, parent TEXT, source TEXT, "
        "verdict TEXT, date TEXT, children TEXT DEFAULT '[]')")
    long_text = "x" * 100
    rows = [
        ("T_full", 0, "Full topic", long_text, None, None),
        ("T_stub", 0, "Stub topic", "tiny", None, None),
        ("F_good", 1, "good.md", "# good\n" + long_text, "T_full", "https://example.com/good.md"),
        ("F_bad", 1, "bad.md", "# bad\n" + long_text, "T_full", "https://example.com/bad.md"),
        ("S1", 2, "good :: s1", "## s1", "F_good", "good.md"),
        ("S2", 2, "good :: s2", "## s2", "F_good", "good.md"),
        ("C1", 3, "good :: s1 chunk", long_text, "S1", "good.md"),
        ("C2", 3, "good :: s2 chunk", long_text, "S2", "good.md"),
        ("C3", 3, "bad :: chunk", long_text, "F_bad", "bad.md"),
        ("F_also", 1, "also.md", "# also\n" + long_text, "T_full", "https://example.com/also.md"),
        ("C4", 3, "also :: chunk", long_text, "F_also", "also.md"),
        ("X_orphan", 2, "orphan", "## orphan", "no-such-id", "ghost.md"),
    ]
    conn.executemany(
        "INSERT INTO factlets (id, level, title, text, parent, source) "
        "VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def selftest():
    """Prove each check trips correctly on a synthetic db. Exit 1 on any miss."""
    tmpdir = tempfile.mkdtemp(prefix="fractal-audit-")
    db = os.path.join(tmpdir, "synthetic.db")
    _mkdb(db)
    r = audit(db, kb_urls={"https://example.com/good.md", "https://example.com/bad.md", "https://example.com/also.md"})

    checks = []

    def expect(name, actual, want):
        checks.append((name, actual == want, actual))

    # rung counts
    expect("level counts", {lv: r["levels"][lv]["count"] for lv in r["levels"]},
           {0: 2, 1: 3, 2: 3, 3: 4})
    # file coverage: good.md has sections+chunks; bad.md and also.md only chunks
    expect("files count", r["files"]["count"], 3)
    expect("sections coverage", r["files"]["with_sections"], 1)   # 33.3% < 80%
    expect("chunks coverage", r["files"]["with_chunks"], 3)       # 100% >= 95%
    expect("sections pct", r["files"]["sections_pct"],
           round(100.0 / 3, 2))
    expect("chunks pct", r["files"]["chunks_pct"], 100.0)
    # topics: 2 topics (1 stub), only 1 of 2 has >=3 children
    expect("topic count", r["topic_health"]["count"], 2)
    expect("topic stubs", r["topic_health"]["stubs"], 1)
    expect("topic min-children pct", r["topic_health"]["pct_with_min_children"], 50.0)
    # orphan: X_orphan -> no-such-id
    expect("orphans", r["orphans"], 1)
    # verdicts trip in the intended directions
    v = r["verdicts"]
    expect("sections FAIL", v["sections"]["pass"], False)
    expect("chunks PASS", v["chunks"]["pass"], True)
    expect("topics FAIL (count+stub)", v["topics"]["pass"], False)
    expect("orphans FAIL", v["orphans"]["pass"], False)
    expect("overall FAIL", v["overall"]["pass"], False)
    expect("exit code is 1", 1 if not v["overall"]["pass"] else 0, 1)

    print(render(r))
    print()
    print("=" * 72)
    print("SELF-TEST: synthetic db asserts ({} checks)".format(len(checks)))
    print("=" * 72)
    ok = True
    for name, passed, actual in checks:
        print("  {}  {}  (got {!r})".format(
            "ok  " if passed else "MISS", name, actual))
        ok = ok and passed
    print("-" * 72)
    print("SELF-TEST RESULT: {}".format(
        "ALL CHECKS TRIP CORRECTLY" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB, help="path to fractal.db")
    ap.add_argument("--json", action="store_true",
                    help="emit the report dict as JSON")
    ap.add_argument("--selftest", action="store_true",
                    help="run the synthetic-db self-test and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    report = audit(args.db)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))
    return 0 if report["verdicts"]["overall"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
