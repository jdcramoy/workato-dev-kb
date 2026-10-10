"""Shared plumbing for the Workato KB crawler: HTTP with ETag caching, paths, logging."""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import sys
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


def fetch_cached(
    url: str, etag: str = "", binary: bool = False
) -> FetchResult:
    """GET with If-None-Match. 304 means unchanged (etag still valid)."""
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
        return FetchResult(url=url, status=0, error=f"HTTP {e.code}")
    except Exception as e:  # noqa: BLE001 - crawler reports all transport errors
        return FetchResult(url=url, status=0, error=str(e))
    if binary:
        return FetchResult(url=url, status=200, text=body.decode("utf-8", "replace"), etag=new_etag)
    return FetchResult(url=url, status=200, text=body.decode("utf-8", "replace"), etag=new_etag)


def fetch_all(jobs: list[tuple[str, str]], progress_every: int = 200) -> list[FetchResult]:
    """jobs: [(url, etag)]. Concurrent fetch with cached etags."""
    results: list[FetchResult] = []
    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_cached, u, e): u for u, e in jobs}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            if progress_every and i % progress_every == 0:
                print(f"  ... {i}/{len(jobs)} fetched", flush=True)
    return results
