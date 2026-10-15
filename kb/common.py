"""Shared plumbing for the Workato KB crawler: HTTP with ETag caching, paths, logging."""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DATA_ROOT = Path(os.environ.get("WORKATO_KB_ROOT", Path.home() / ".claude/workato-kb"))
REPO_ROOT = Path(__file__).resolve().parent.parent

UA = "workato-kb/0.1 (local; personal use)"
TIMEOUT = 30
MAX_WORKERS = 12


def corpus_dir(source: str) -> Path:
    return DATA_ROOT / "corpus" / source


def manifest_path(source: str) -> Path:
    return DATA_ROOT / "manifest" / f"{source}.jsonl"


def load_manifest(source: str) -> dict[str, dict]:
    """url -> record. Manifests are JSONL, one {url: ...} object per line."""
    p = manifest_path(source)
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if not isinstance(rec, dict) or not rec.get("url"):
            continue  # corrupt row: one url-less record must not kill the nightly
        out[rec["url"]] = rec
    return out


def save_manifest(source: str, records: dict[str, dict]) -> None:
    p = manifest_path(source)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as f:
        for url in sorted(records):
            f.write(json.dumps(records[url], sort_keys=True) + "\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class FetchResult:
    url: str
    status: int  # 200 fetched, 304 unchanged, 0 error
    text: str = ""
    etag: str = ""
    error: str = ""
    retry_after: float = 0.0  # server-supplied cooldown (429 responses only)


# --- 429 circuit-breaker (2026-10-15: Hugging Face rate-limits bulk doc
# fetches with retry-after: 60; Slack flakes similarly). When any fetch sees
# a 429, ALL fetches in the process sleep through the cooldown window before
# retrying — a 12-worker pool that retries independently just re-trips the
# limit in a synchronized burst.
_rate_lock = threading.Lock()
_cool_until = 0.0
MAX_429_ATTEMPTS = 4


def _cooldown(seconds: float) -> None:
    global _cool_until
    with _rate_lock:
        _cool_until = max(_cool_until, time.time() + seconds)


def _wait_cooldown() -> float:
    with _rate_lock:
        return max(0.0, _cool_until - time.time())


def fetch_cached(
    url: str, etag: str = "", binary: bool = False
) -> FetchResult:
    """GET with If-None-Match. 304 means unchanged (etag still valid).

    HTTP 429 engages a process-wide cooldown (server Retry-After when sent,
    else exponential 4s/8s/16s) and retries up to 4 attempts. Other error
    statuses surface as status=0."""
    result = None
    for attempt in range(MAX_429_ATTEMPTS):
        wait = _wait_cooldown()
        if wait:
            time.sleep(wait)
        result = _fetch_once(url, etag, binary)
        if result.status != 0 or "HTTP 429" not in result.error:
            return result
        _cooldown(result.retry_after or 4 * (2 ** attempt))
    return result


def _fetch_once(url: str, etag: str, binary: bool) -> FetchResult:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            new_etag = resp.headers.get("ETag", "")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return FetchResult(url=url, status=304, etag=etag)
        ra = 0.0
        try:
            ra = float(e.headers.get("Retry-After", "") or 0)
        except (TypeError, ValueError):
            pass
        return FetchResult(url=url, status=0, error=f"HTTP {e.code}", retry_after=ra)
    except Exception as e:  # noqa: BLE001 - crawler reports all transport errors
        return FetchResult(url=url, status=0, error=str(e))
    if binary:
        return FetchResult(url=url, status=200, text=body.decode("utf-8", "replace"), etag=new_etag)
    return FetchResult(url=url, status=200, text=body.decode("utf-8", "replace"), etag=new_etag)


def fetch_all(jobs: list[tuple[str, str]], progress_every: int = 200) -> list[FetchResult]:
    """jobs: [(url, etag)]. Concurrent fetch with cached etags.

    Returns results IN JOB ORDER (zip-with-jobs is the callers' universal
    assumption; as_completed alone scrambles them — bit the workday source
    2026-10-15, asor spec written under the absenceManagement path)."""
    by_url: dict[str, FetchResult] = {}
    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_cached, u, e): u for u, e in jobs}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            r = fut.result()
            by_url.setdefault(r.url, r)  # first result wins on duplicate urls
            if progress_every and i % progress_every == 0:
                print(f"  ... {i}/{len(jobs)} fetched", flush=True)
    return [by_url.get(u, FetchResult(url=u, status=0, error="no result")) for u, _ in jobs]
