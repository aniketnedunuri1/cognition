from datetime import date

from conftest import load
from server import cache, exa
from server.cache import get_doc

TODAY = date(2026, 10, 7)


def person(fixture, name):
    for r in load(fixture)["results"]:
        if r["title"].startswith(name):
            return exa._person_entity(r)
    raise KeyError(name)


def test_current_role_from_structured_history():
    e = exa.employment_at(person("exa_people_brex.json", "James Reggio"), "Brex", TODAY)
    assert e["is_current"] is True
    assert e["current_title"] == "Chief Technology Officer"
    assert e["start_date"] == "2024-09"
    assert e["months_in_role"] == 25


def test_overlapping_current_roles_take_latest_start():
    e = exa.employment_at(person("exa_people_brex.json", "Sonny Gupta"), "Brex", TODAY)
    assert e["current_title"] == "Director of Engineering"


def test_former_employee_is_not_current():
    e = exa.employment_at(person("exa_people_brex.json", "Will Palmeri"), "Brex", TODAY)
    assert e["is_current"] is False
    assert "past employer" in e["reason"]


def test_moved_company_is_not_current():
    e = exa.employment_at(person("exa_people_ramp.json", "Nik Koblov"), "Ramp", TODAY)
    assert e["is_current"] is not True


def test_missing_structure_is_unknown_never_guessed():
    e = exa.employment_at(person("exa_people_ramp.json", "Claudio Buzzoni"), "Ramp", TODAY)
    assert e == {"is_current": "unknown", "current_title": None, "start_date": "unknown",
                 "months_in_role": "unknown", "reason": "no structured work history in profile"}


def test_similar_company_name_is_unknown():
    props = {"workHistory": [{"title": "VP Eng", "company": {"name": "Ramp Network"},
                              "dates": {"from": "2025-01-01", "to": None}}]}
    assert exa.employment_at(props, "Ramp", TODAY)["is_current"] == "unknown"


def test_unparseable_start_date_is_unknown():
    props = {"workHistory": [{"title": "CTO", "company": {"name": "Brex, Inc."},
                              "dates": {"from": "sometime", "to": None}}]}
    e = exa.employment_at(props, "Brex", TODAY)
    assert e["is_current"] is True and e["start_date"] == "unknown" and e["months_in_role"] == "unknown"


def test_search_people_merges_roles_and_stays_compact(stub):
    calls = stub("server.exa._post", lambda path, body: load("exa_people_ramp.json"))
    out = exa.search_people("Ramp", ["VP of Engineering", "CTO"])
    assert len(calls) == 2
    people = out["people"]
    assert len(people) + out["former_employee_count"] + out["not_at_company_count"] == 10  # merged, none lost
    assert all(get_doc(p["source_id"])["kind"] == "person" for p in people)
    assert all(p["is_current"] in (True, "unknown") for p in people)
    assert "Claudio Buzzoni" in {p["name"].split(" -")[0] for p in people}  # no structure -> unknown, kept
    assert set(people[0]) == {"source_id", "name", "current_title", "is_current", "start_date",
                              "months_in_role", "previous_role", "ex_devin_customers", "highlight"}
    assert all(len(p["highlight"]) <= exa.HIGHLIGHT_CHARS for p in people)


def test_ex_devin_customers_matched_in_code(stub, tmp_path, monkeypatch):
    import json
    kd = tmp_path / "k"; kd.mkdir()
    (kd / "customers.json").write_text(json.dumps({"customers": [
        {"name": "Meta", "public_reference": False, "proofs": []},
        {"name": "Vitally Inc", "public_reference": True, "proofs": []}]}))
    monkeypatch.setenv("KNOWLEDGE_DIR", str(kd))
    stub("server.exa._post", lambda path, body: load("exa_people_brex.json"))
    phil = next(p for p in exa.search_people("Brex", ["platform"])["people"] if p["name"] == "Phil Burrows")
    assert phil["ex_devin_customers"] == [{"customer": "Meta", "public_reference": False}]
    assert phil["previous_role"] == "Engineering Leader at Meta"


def test_fetch_page_caches_full_text_returns_excerpt(stub):
    text = "word " * 3000
    stub("server.exa._post", lambda path, body: {
        "results": [{"url": "https://example.com/post", "title": "Post", "text": text}],
        "statuses": [{"id": "https://example.com/post", "status": "success"}]})
    page = exa.fetch_page("https://example.com/post")
    assert page["status"] == "ok"
    assert len(page["excerpt"]) == exa.EXCERPT_CHARS and page["next_offset"] == exa.EXCERPT_CHARS
    assert get_doc(page["source_id"])["text"] == text


def test_fetch_failure_is_reported_and_not_cached(stub):
    calls = stub("server.exa._post", lambda path, body: {
        "results": [], "statuses": [{"id": "u", "status": "error", "error": {"tag": "CRAWL_NOT_FOUND"}}]})
    assert exa.fetch_page("https://linkedin.com/jobs/view/1")["status"] == "not_fetched"
    exa.fetch_page("https://linkedin.com/jobs/view/1")
    assert len(calls) == 2  # failure wasn't cached


def test_force_live_crawls_and_replaces_stale_copy(stub):
    bodies = []
    def fake(path, body):
        bodies.append(body)
        text = "fresh story" if body.get("maxAgeHours") == 0 else "stale story"
        return {"results": [{"url": body["urls"][0], "text": text}], "statuses": [{"status": "success"}]}
    stub("server.exa._post", fake)
    assert exa.fetch_page("https://devin.ai/customers/x")["excerpt"] == "stale story"
    assert exa.fetch_page("https://devin.ai/customers/x", force=True)["excerpt"] == "fresh story"
    assert exa.fetch_page("https://devin.ai/customers/x")["excerpt"] == "fresh story"  # replaced, not shadowed
    assert len(bodies) == 2


def test_search_web_include_domains_is_sent(stub):
    bodies = []
    stub("server.exa._post", lambda path, body: (bodies.append(body) or {
        "results": [{"url": "https://www.linkedin.com/posts/x", "title": "A talk",
                     "publishedDate": "2026-09-01T00:00:00Z", "highlights": ["podcast"]}]}))
    rows = exa.search_web("Phil Burrows Brex talk OR interview", include_domains=["linkedin.com"])
    assert bodies[0]["includeDomains"] == ["linkedin.com"]
    assert rows[0]["site"] == "linkedin.com"
    exa.search_web("Brex news")
    assert "includeDomains" not in bodies[1]


# ---------- search_company_linkedin ----------

LI = {
    "posts": [{"url": "https://www.linkedin.com/posts/brexhq_launch-activity-111",
               "title": "Brex launches a thing", "publishedDate": "2026-09-15T00:00:00Z",
               "highlights": ["Today we launched something new"]}],
    "mentions": [{"url": "https://www.linkedin.com/company/brexhq/posts/?feedView=all",
                  "title": "Event recap", "publishedDate": "2026-09-10T00:00:00Z",
                  "highlights": ["Great day with @Jane Doe linkedin.com/in/janedoe"]}],
    "reposts": [{"url": "https://www.linkedin.com/posts/someone_else-999",
                 "title": "A share", "publishedDate": "2026-09-01T00:00:00Z",
                 "highlights": ["Brex reposted this"]}],
}


def _li_stub(stub, responses, bodies):
    def fake(path, body):
        bodies.append(body)
        q = body["query"]
        key = "mentions" if "tagged OR mentioned" in q else \
              "reposts" if "reposted OR reshared" in q else "posts"
        return {"results": responses.get(key, [])}
    return stub("server.exa._post", fake)


def test_slug_drives_path_prefix_domains(stub):
    bodies = []
    _li_stub(stub, LI, bodies)
    out = exa.search_company_linkedin("Brex", linkedin_slug="brexhq")
    assert out["slug"] == "brexhq"
    assert len(bodies) == 3
    assert all(b["includeDomains"] == ["linkedin.com/posts/brexhq", "linkedin.com/company/brexhq"]
               for b in bodies)
    assert any("post OR announcement OR news" in b["query"] for b in bodies)


def test_slug_resolved_from_cached_apollo_org(stub):
    cache.upsert_doc(cache.make_doc(identity="apollo:org:brex.com", kind="apollo_org",
                                    label="Apollo", retrieved_at=cache.now_iso(),
                                    data={"linkedin": "http://www.linkedin.com/company/brexhq"}))
    bodies = []
    _li_stub(stub, LI, bodies)
    out = exa.search_company_linkedin("Brex", domain="brex.com")
    assert out["slug"] == "brexhq"
    assert bodies[0]["includeDomains"][0] == "linkedin.com/posts/brexhq"


def test_no_slug_still_searches_without_inventing_one(stub):
    bodies = []
    _li_stub(stub, LI, bodies)
    out = exa.search_company_linkedin("Brex", domain="brex.com")
    assert out["slug"] is None
    assert all(b["includeDomains"] == ["linkedin.com"] for b in bodies)


def test_kind_and_tags_from_url_and_snippet_only(stub):
    _li_stub(stub, LI, [])
    out = exa.search_company_linkedin("Brex", linkedin_slug="brexhq")
    kinds = {r["title"]: (r["kind"], r["possible_tags"]) for r in out["posts"]}
    assert kinds["Brex launches a thing"] == ("original", [])
    assert kinds["Event recap"][0] == "original"  # /company/<slug> URL
    assert set(kinds["Event recap"][1]) == {"linkedin.com/in/janedoe", "Jane Doe"}
    assert kinds["A share"] == ("possible_repost", [])


def test_company_linkedin_query_failure_is_reported_not_guessed(stub):
    def fake(path, body):
        if "reposted OR reshared" in body["query"]:
            raise exa.ProviderError("boom")
        return {"results": []}
    stub("server.exa._post", fake)
    out = exa.search_company_linkedin("Brex", linkedin_slug="brexhq")
    assert out["not_fetched"] == ["reposts"] and out["posts"] == []


def test_company_linkedin_since_is_sent(stub):
    bodies = []
    _li_stub(stub, {}, bodies)
    exa.search_company_linkedin("Brex", linkedin_slug="brexhq", since="2026-04-01")
    assert all(b["startPublishedDate"] == "2026-04-01" for b in bodies)
