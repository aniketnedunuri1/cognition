import json
from datetime import datetime, timedelta, timezone

import pytest

from server import cache


def docs_for(response, fetched_at):
    return [cache.make_doc(identity=r["url"], kind="web", url=r["url"], highlights=r["h"],
                           retrieved_at=fetched_at) for r in response["results"]]


RESP = {"results": [{"url": "https://www.Example.com/a/", "h": ["one"]}]}


def call(fetch):
    return cache.cached_call("exa/search", {"query": "q"}, "discovery", fetch, docs_for)


def test_hit_skips_fetch_and_maps_request_to_docs(isolated_cache):
    calls = []
    fetch = lambda: calls.append(1) or RESP
    docs, _ = call(fetch)
    docs2, _ = call(fetch)
    assert len(calls) == 1
    assert docs[0]["source_id"] == docs2[0]["source_id"]
    entry = json.loads(next((isolated_cache / "requests").iterdir()).read_text())
    assert entry["doc_ids"] == [docs[0]["source_id"]]


def test_expired_entry_refetches(isolated_cache):
    call(lambda: RESP)
    path = next((isolated_cache / "requests").iterdir())
    entry = json.loads(path.read_text())
    entry["fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    path.write_text(json.dumps(entry))
    calls = []
    call(lambda: calls.append(1) or RESP)
    assert calls == [1]


def test_lookup_ttl_is_longer_than_discovery():
    assert cache.TTL["lookup"] > cache.TTL["discovery"]


def test_replay_never_calls_api(monkeypatch):
    monkeypatch.setenv("CACHE_MODE", "replay")
    with pytest.raises(cache.CacheMiss):
        call(lambda: pytest.fail("fetched in replay mode"))


def test_replay_uses_expired_entries(isolated_cache, monkeypatch):
    call(lambda: RESP)
    path = next((isolated_cache / "requests").iterdir())
    entry = json.loads(path.read_text())
    entry["fetched_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(entry))
    monkeypatch.setenv("CACHE_MODE", "replay")
    docs, _ = call(lambda: pytest.fail("fetched in replay mode"))
    assert docs


def test_source_id_is_per_document_not_per_request():
    a = cache.source_id_for("https://www.example.com/a/")
    assert a == cache.source_id_for("https://example.com/a")
    assert a != cache.source_id_for("https://example.com/b")


def test_upsert_merges_highlights_and_keeps_text():
    base = dict(identity="https://x.com/p", kind="web", url="https://x.com/p")
    cache.upsert_doc(cache.make_doc(**base, highlights=["a"], retrieved_at="2026-01-01T00:00:00+00:00"))
    cache.upsert_doc(cache.make_doc(**base, text="full", retrieved_at="2026-01-02T00:00:00+00:00"))
    doc = cache.upsert_doc(cache.make_doc(**base, highlights=["a", "b"], retrieved_at="2026-01-01T00:00:00+00:00"))
    assert doc["highlights"] == ["a", "b"]
    assert doc["text"] == "full"
    assert doc["retrieved_at"].startswith("2026-01-02")


def test_get_doc_rejects_malformed_ids():
    assert cache.get_doc("../../etc/passwd") is None
