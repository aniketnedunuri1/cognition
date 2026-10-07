import json
import shutil
from pathlib import Path

import pytest

from server import cache, knowledge

ROOT = Path(__file__).resolve().parent.parent
SITEMAP = """<urlset>
<url><loc>https://cognition.com/blog/cobol</loc><lastmod>2026-08-12T00:00:00Z</lastmod></url>
<url><loc>https://cognition.com/blog/swe-1-7</loc><lastmod>2026-08-12T00:00:00Z</lastmod></url>
<url><loc>https://cognition.com/legal/terms</loc><lastmod>2026-08-12T00:00:00Z</lastmod></url>
</urlset>"""
STORY = "Acme Bank used Devin to migrate 400 COBOL programs to Java in six weeks."


@pytest.fixture
def kb(tmp_path, monkeypatch, stub):
    d = tmp_path / "knowledge"
    d.mkdir()
    shutil.copy(ROOT / "knowledge" / "sources.json", d / "sources.json")
    monkeypatch.setenv("KNOWLEDGE_DIR", str(d))
    state = {"sitemap": SITEMAP, "text": STORY}
    monkeypatch.setattr(knowledge, "_sitemap", lambda url: knowledge.re.findall(
        r"<loc>([^<]+)</loc><lastmod>([^<]+)</lastmod>", state["sitemap"]) if "cognition" in url else [])
    calls = stub("server.exa._post", lambda path, body: (
        {"results": [{"url": body["urls"][0], "title": "COBOL", "text": state["text"],
                      "publishedDate": "2026-08-01T00:00:00Z"}],
         "statuses": [{"id": body["urls"][0], "status": "success"}]} if path == "/contents" else {"results": []}))
    return d, state, calls


def web_doc(url, text):
    return cache.upsert_doc(cache.make_doc(identity=url, kind="web", url=url, text=text,
                                           retrieved_at=cache.now_iso()))["source_id"]


def test_refresh_filters_and_apply_marks_seen(kb):
    d, state, calls = kb
    out = knowledge.refresh_positioning()
    assert [i["path"] for i in out["items"]] == ["/blog/cobol"]  # swe- excluded, legal not included
    sid = out["items"][0]["source_id"]
    knowledge.apply_knowledge_update([sid], "init", customers=[
        {"name": "Acme Bank", "use_case": "COBOL migration", "quote": "migrate 400 COBOL programs to Java", "source_id": sid}])
    assert knowledge.refresh_positioning()["items"] == []
    seen = json.loads((d / "seen.json").read_text())
    assert seen["last_refresh"] and not seen["pending"]


def test_rebuilt_lastmod_with_same_content_is_not_reprocessed(kb):
    d, state, calls = kb
    sid = knowledge.refresh_positioning()["items"][0]["source_id"]
    knowledge.apply_knowledge_update([sid], "init")
    state["sitemap"] = SITEMAP.replace("2026-08-12", "2026-10-01")
    assert knowledge.refresh_positioning()["items"] == []  # refetched, same hash
    state["sitemap"] = SITEMAP.replace("2026-08-12", "2026-10-02")
    state["text"] = STORY + " Now 900 programs."
    assert knowledge.refresh_positioning()["items"][0]["change"] == "changed"


def test_public_reference_comes_from_domain_not_model(kb):
    own = web_doc("https://devin.ai/customers/acme", STORY)
    press = web_doc("https://techcrunch.com/acme", "Globex says it used Devin to clear its Java backlog quickly.")
    r = knowledge.apply_knowledge_update([], "x", customers=[
        {"name": "Acme Bank", "use_case": "Migration", "quote": "migrate 400 COBOL programs to Java", "source_id": own,
         "public_reference": False},
        {"name": "Globex", "use_case": "Backlog", "quote": "used Devin to clear its Java backlog", "source_id": press,
         "public_reference": True},
    ])
    assert r["customers_added"] == 2
    matches = {m["customer"]: m["public_reference"] for m in knowledge.match_customers(["Acme Bank, Inc.", "Globex", "Nobody"])}
    assert matches == {"Acme Bank": True, "Globex": False}
    assert [c["name"] for c in knowledge.get_positioning()["public_customers"]] == ["Acme Bank"]


def test_unverified_quotes_and_third_party_use_cases_rejected(kb):
    own = web_doc("https://cognition.com/blog/x", STORY)
    press = web_doc("https://news.example.com/x", "Teams use Devin to write unit tests for legacy code.")
    r = knowledge.apply_knowledge_update([], "x",
        customers=[{"name": "Acme Bank", "use_case": "m", "quote": "migrated a million programs overnight", "source_id": own}],
        use_cases=[{"name": "Test generation", "description": "d", "quote": "use Devin to write unit tests", "source_id": press},
                   {"name": "COBOL migration", "description": "d", "quote": "migrate 400 COBOL programs", "source_id": own}])
    assert r["customers_added"] == 0 and r["use_cases_added"] == 1
    assert len(r["rejected"]) == 2
    assert knowledge.use_case_names() == {"COBOL migration"}


def test_positioning_rewrite_needs_cognition_sources(kb):
    d, *_ = kb
    own = web_doc("https://cognition.com/blog/x", STORY)
    press = web_doc("https://news.example.com/x", "Devin is the best agent ever built.")
    r = knowledge.apply_knowledge_update([], "x", positioning_markdown=f"Devin is great [{press}]")
    assert not r["positioning_rewritten"] and not (d / "positioning.md").exists()
    r = knowledge.apply_knowledge_update([], "shift", positioning_markdown=f"Devin migrates COBOL [{own}]")
    assert r["positioning_rewritten"] and r["slack"] == "logged"
    assert r["committed"] is False  # tmp dir is not a git repo


def test_refresh_refuses_replay(kb, monkeypatch):
    monkeypatch.setenv("CACHE_MODE", "replay")
    with pytest.raises(knowledge.ProviderError):
        knowledge.refresh_positioning()


def test_customer_must_be_named_in_page_body(kb):
    wrong = cache.upsert_doc(cache.make_doc(
        identity="https://devin.ai/customers/hamming", kind="web", url="https://devin.ai/customers/hamming",
        title="Customers | Hamming | Devin", text="Customers | Hamming | Devin " + STORY,
        retrieved_at=cache.now_iso()))["source_id"]
    r = knowledge.apply_knowledge_update([], "x", customers=[
        {"name": "Hamming", "use_case": "m", "quote": "migrate 400 COBOL programs to Java", "source_id": wrong}])
    assert r["customers_added"] == 0 and "not named in the page body" in r["rejected"][0]["reason"]
