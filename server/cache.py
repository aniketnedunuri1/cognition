"""Two-layer cache.

Request layer (cache/requests/<key>.json): the raw provider response for one
request, keyed by endpoint + request body. Used for replay and TTL refresh.

Document layer (cache/docs/<source_id>.json): one citable thing — a web page,
a people profile, a job post, an Apollo org record. The model cites source_ids
only; URLs and dates are filled in from here at render time.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent

# Discovery = "what's out there" (searches, job lists): changes daily.
# Lookup = a known thing (a fetched page, firmographics): changes slowly.
TTL = {"discovery": timedelta(days=1), "lookup": timedelta(days=7)}
MODES = ("default", "replay", "fresh")

_lock = threading.Lock()


class CacheMiss(Exception):
    """Replay mode and the request isn't cached."""


def cache_dir() -> Path:
    return Path(os.environ.get("CACHE_DIR", ROOT / "cache"))


def mode() -> str:
    """default: reuse unexpired entries. replay: cache only. fresh: always refetch."""
    m = os.environ.get("CACHE_MODE", "default")
    if m not in MODES:
        raise ValueError(f"CACHE_MODE must be one of {MODES}, got {m!r}")
    return m


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


# ---------- request layer ----------

def request_key(endpoint: str, request: dict) -> str:
    blob = json.dumps({"endpoint": endpoint, "request": request}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _expired(entry: dict) -> bool:
    fetched = datetime.fromisoformat(entry["fetched_at"])
    return datetime.now(timezone.utc) - fetched > TTL[entry["ttl_class"]]


def cached_call(
    endpoint: str,
    request: dict,
    ttl_class: str,
    fetch: Callable[[], dict],
    to_docs: Callable[[dict, str], list[dict]],
    force: bool = False,
) -> tuple[list[dict], dict]:
    """Return (documents, raw response) for a request, fetching only if needed.

    fetch() makes the live call. to_docs(response, fetched_at) turns the raw
    response into document records; they're upserted on every call (idempotent)
    so the doc layer can always be rebuilt from the request layer.
    force=True refetches regardless of TTL (a source reported a change), except in replay.
    """
    key = request_key(endpoint, request)
    path = cache_dir() / "requests" / f"{key}.json"
    entry = _read(path)
    m = mode()

    if entry and (m == "replay" or (m == "default" and not force and not _expired(entry))):
        response, fetched_at = entry["response"], entry["fetched_at"]
    elif m == "replay":
        raise CacheMiss(f"{endpoint} request not in cache (replay mode)")
    else:
        response, fetched_at = fetch(), now_iso()

    docs = [upsert_doc(d) for d in to_docs(response, fetched_at)]
    _write(path, {
        "endpoint": endpoint,
        "request": request,
        "ttl_class": ttl_class,
        "fetched_at": fetched_at,
        "doc_ids": [d["source_id"] for d in docs],
        "response": response,
    })
    return docs, response


# ---------- document layer ----------

def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+$", "", parts.path)
    return urlunsplit(("https", host, path, parts.query, ""))


def source_id_for(identity: str) -> str:
    """Stable id for a document. identity is a URL or e.g. 'apollo:org:brex.com'."""
    ident = canonical_url(identity) if identity.startswith("http") else identity
    return "src_" + hashlib.sha1(ident.encode()).hexdigest()[:10]


def make_doc(
    *,
    identity: str,
    kind: str,
    retrieved_at: str,
    url: str | None = None,
    title: str | None = None,
    published_date: str | None = None,
    highlights: list[str] | None = None,
    text: str | None = None,
    data: dict | None = None,
    label: str | None = None,
) -> dict:
    """kind: web | person | job | apollo_org. label: how to cite non-URL sources."""
    return {
        "source_id": source_id_for(identity),
        "kind": kind,
        "url": url,
        "label": label,
        "title": title,
        "published_date": published_date,
        "retrieved_at": retrieved_at,
        "highlights": highlights or [],
        "text": text,
        "data": data or {},
    }


_KIND_RANK = {"web": 0, "job": 1, "person": 1, "apollo_org": 1}


def upsert_doc(new: dict) -> dict:
    """Merge into any existing record: the same page can come back from several
    searches (each with query-specific highlights) and later from fetch_page."""
    path = cache_dir() / "docs" / f"{new['source_id']}.json"
    with _lock:
        old = _read(path)
        if old:
            merged = dict(old)
            for k in ("url", "label", "title", "published_date"):
                merged[k] = new.get(k) or old.get(k)
            if _KIND_RANK[new["kind"]] > _KIND_RANK[old["kind"]]:
                merged["kind"] = new["kind"]
            merged["highlights"] = old["highlights"] + [
                h for h in new["highlights"] if h not in old["highlights"]
            ]
            merged["text"] = new.get("text") or old.get("text")
            merged["data"] = {**old["data"], **new["data"]}
            merged["retrieved_at"] = max(old["retrieved_at"], new["retrieved_at"])
        else:
            merged = new
        _write(path, merged)
    return merged


def get_doc(source_id: str) -> dict | None:
    if not re.fullmatch(r"src_[0-9a-f]{10}", source_id or ""):
        return None
    return _read(cache_dir() / "docs" / f"{source_id}.json")


def site(url: str | None) -> str | None:
    return urlsplit(url).netloc.lower().removeprefix("www.") if url else None
