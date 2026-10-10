"""LLM-generated contextual headers (roadmap item, Anthropic-style contextual retrieval).

For each doc, a local LLM (ollama, qwen2.5:3b-instruct) writes a one-sentence
"situating" line: what this doc covers and what task it supports. Stored on the
docs row; kb.indexer prepends it to every chunk context, so embeddings and FTS
see situation, not just keywords.

Incremental + capped: NEW/CHANGED docs only, max --limit per run (~1h per 400
docs on MPS). Re-run kb.indexer afterwards to fold new contexts into chunks.

    python3 -m kb.contextualize --limit 25   # bounded test
    python3 -m kb.contextualize              # drain up to 400/night
    python3 -m kb.contextualize --stats
"""

from __future__ import annotations

import argparse
import json
import urllib.request

from .common import DATA_ROOT
from .indexer import get_db

MODEL = "qwen2.5:3b-instruct"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
PROMPT = (
    "You write ONE short sentence (max 30 words) summarizing what a documentation "
    "page covers and what task it helps a reader accomplish. "
    "No preamble, no quotes, one sentence only. Do not use the word 'retrieval'.\n\n"
    "Page title: {title}\nSource: {source} / section: {section}\n\nPage content:\n{body}"
)


def _ollama_ready() -> bool:
    try:
        out = subprocess_run_list()
        return MODEL.split(":")[0] in out
    except Exception:  # noqa: BLE001
        return False


def subprocess_run_list() -> str:
    import subprocess

    out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
    return out.stdout if out.returncode == 0 else ""


def _generate(doc: dict, body: str) -> str:
    payload = json.dumps(
        {
            "model": MODEL,
            "prompt": PROMPT.format(
                title=doc["title"], source=doc["source"], section=doc["section"], body=body[:6000]
            ),
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 60},
        }
    ).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp).get("response", "").strip().splitlines()[0][:300]


def run(limit: int = 400) -> dict:
    if not _ollama_ready():
        return {"skipped": f"ollama/{MODEL} unavailable — start `brew services start ollama` and `ollama pull {MODEL}`"}
    db = get_db()
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(docs)")}
        if "llm_context" not in cols:
            db.execute("ALTER TABLE docs ADD COLUMN llm_context TEXT DEFAULT ''")
        db.commit()
        rows = db.execute(
            "SELECT doc_id, source, url, title, section, path FROM docs "
            "WHERE (llm_context IS NULL OR llm_context = '') LIMIT ?",
            (limit,),
        ).fetchall()
        done, failed = 0, []
        for i, (doc_id, source, url, title, section, path) in enumerate(rows, 1):
            f = DATA_ROOT / "corpus" / path
            if not f.exists():
                continue
            try:
                ctx = _generate(
                    {"title": title, "source": source, "section": section}, f.read_text()
                )
                # zero the stored sha so the next indexer run re-chunks with context
                db.execute("UPDATE docs SET llm_context=?, sha256='' WHERE doc_id=?", (ctx, doc_id))
                db.commit()
                done += 1
                if i % 25 == 0:
                    print(f"  ... {i}/{len(rows)} contextualized", flush=True)
            except Exception as e:  # noqa: BLE001 - nightly job records, continues
                failed.append({"url": url, "error": str(e)[:120]})
        (total_pending,) = db.execute(
            "SELECT COUNT(*) FROM docs WHERE (llm_context IS NULL OR llm_context = '')"
        ).fetchone()
        return {"contextualized": done, "failed": failed[:10], "pending": total_pending}
    finally:
        db.close()


def stats() -> dict:
    db = get_db()
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(docs)")}
        if "llm_context" not in cols:
            return {"contextualized": 0, "pending": db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]}
        (done,) = db.execute(
            "SELECT COUNT(*) FROM docs WHERE llm_context IS NOT NULL AND llm_context != ''"
        ).fetchone()
        (total,) = db.execute("SELECT COUNT(*) FROM docs").fetchone()
        return {"contextualized": done, "pending": total - done}
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    print(json.dumps(stats() if a.stats else run(a.limit), indent=2))
