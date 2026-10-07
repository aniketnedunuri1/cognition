from datetime import date

from conftest import load
from server import apollo, cache, exa
from server.schema import validate_brief

TODAY = date(2026, 10, 7)
POST = "We are migrating our monolith from Java 8 to Java 17 this quarter. Phil Burrows leads platform."


def web(url, text=POST, published="2026-08-01T00:00:00Z"):
    return cache.upsert_doc(cache.make_doc(identity=url, kind="web", url=url, text=text,
                                           published_date=published, retrieved_at=cache.now_iso()))["source_id"]


def profiles(stub):
    stub("server.exa._post", lambda path, body: load("exa_people_brex.json"))
    return {p["name"]: p["source_id"] for p in exa.search_people("Brex", ["platform"])}


def person(name, *ids, **kw):
    return {"name": name, "title": "t", "deal_role": "champion", "why": "w",
            "evidence": [{"source_id": i} for i in ids], **kw}


def run(raw):
    return validate_brief({"domain": "brex.com", "company_name": "Brex", **raw}, today=TODAY)


def test_unsourced_and_unresolvable_facts_dropped():
    good = web("https://brex.com/blog/a")
    brief, report = run({"snapshot": {"hq": [
        {"value": "San Francisco", "evidence": [{"source_id": good}]},
        {"value": "Made up", "evidence": []},
        {"value": "Fake id", "evidence": [{"source_id": "src_deadbeef00"}]},
        {"value": "Model-written URL", "evidence": [{"source_id": "https://brex.com"}]},
    ]}})
    assert [f["value"] for f in brief["snapshot"]["hq"]] == ["San Francisco"]
    assert report["dropped_count"] >= 3


def test_signal_needs_verified_recent_quote():
    fresh = web("https://brex.com/blog/fresh")
    old = web("https://brex.com/blog/old", published="2025-01-01T00:00:00Z")
    sig = {"headline": "Java 17 migration", "type": "migration", "use_case": "Migrations",
           "strength": "strong"}
    brief, report = run({"signals": [
        {**sig, "horizon": "tactical", "evidence": [{"source_id": fresh, "quote": "migrating our monolith from Java 8 to Java 17"}]},
        {**sig, "horizon": "tactical", "evidence": [{"source_id": fresh}]},
        {**sig, "horizon": "tactical", "evidence": [{"source_id": fresh, "quote": "rewriting everything in Rust now"}]},
        {**sig, "horizon": "strategic", "evidence": [{"source_id": old, "quote": "migrating our monolith from Java 8 to Java 17"}]},
    ]})
    assert len(brief["signals"]) == 1
    reasons = " ".join(d["reason"] for d in report["dropped"])
    assert "needs an exact quote" in reasons and "failed verification" in reasons and "older than 12 months" in reasons


def test_use_case_must_exist_when_knowledge_loaded():
    src = web("https://brex.com/blog/fresh")
    raw = {"domain": "brex.com", "company_name": "Brex", "signals": [{
        "headline": "h", "type": "migration", "horizon": "tactical", "use_case": "Vibes", "strength": "medium",
        "evidence": [{"source_id": src, "quote": "migrating our monolith from Java 8"}]}]}
    brief, _ = validate_brief(raw, today=TODAY, use_cases={"Migrations"})
    assert brief["signals"] == []


def test_people_rules(stub):
    ids = profiles(stub)
    blog = web("https://brex.com/blog/fresh")
    brief, report = run({"people": [
        person("Phil Burrows", ids["Phil Burrows"]),                       # current per profile
        person("Will Palmeri", ids["Will Palmeri"]),                       # former, single source
        person("Jane Invented", ids["Phil Burrows"]),                      # name not in source
        person("Phil Burrows", ids["Phil Burrows"], blog,                  # verified + champion evidence
               champion_evidence=[{"value": "Leads platform", "evidence": [{"source_id": blog, "quote": "Phil Burrows leads platform"}]}]),
    ]})
    names = [p["name"] for p in brief["people"]]
    assert names == ["Phil Burrows", "Phil Burrows"]
    first, second = brief["people"]
    assert first["verified"] is False and first["label"] == "candidate"
    assert second["verified"] is True and second["label"] == "likely_champion"
    assert first["profile_source_id"] == ids["Phil Burrows"]
    reasons = " ".join(d["reason"] for d in report["dropped"])
    assert "current role not confirmed" in reasons and "not found in any cited source" in reasons


def test_top_contacts_must_be_validated_people(stub):
    ids = profiles(stub)
    why = {"text": "Leads platform", "based_on": [{"source_id": ids["Phil Burrows"]}]}
    brief, _ = run({
        "people": [person("Phil Burrows", ids["Phil Burrows"])],
        "top_contacts": [{"name": "Phil Burrows", "why": why}, {"name": "Ghost Person", "why": why}],
    })
    assert [t["name"] for t in brief["top_contacts"]] == ["Phil Burrows"]


def test_verdict_rules():
    src = web("https://brex.com/blog/fresh")
    brief, _ = run({"verdict": {"verdict": "act_now", "why_now": "x", "based_on": [{"source_id": "src_0123456789"}]}})
    assert brief["verdict"] is None
    brief, _ = run({"verdict": {"verdict": "not_now", "why_now": "No qualifying signals found"}})
    assert brief["verdict"]["verdict"] == "not_now"
    brief, _ = run({"verdict": {"verdict": "warm", "why_now": "x", "based_on": [{"source_id": src}]}})
    assert brief["verdict"]["verdict"] == "warm"


def test_malformed_items_dropped_not_fatal():
    brief, report = run({"signals": [{"headline": "missing everything"}], "people": ["not an object"]})
    assert brief["signals"] == [] and brief["people"] == []
    assert report["dropped_count"] == 2


def test_title_mismatch_with_profile_is_flagged(stub):
    ids = profiles(stub)
    _, report = run({"people": [{**person("Phil Burrows", ids["Phil Burrows"]), "title": "CTO"}]})
    assert any("title differs" in f["flag"] for f in report["flags"])
    _, report = run({"people": [{**person("Phil Burrows", ids["Phil Burrows"]),
                                 "title": "Head of Platform & Infra Engineering"}]})
    assert not any("title differs" in f["flag"] for f in report["flags"])


ANNOUNCEMENT = "Brex today announced that Jane Newleader has joined as Chief Technology Officer."


def test_single_official_company_source_lists_person():
    own = web("https://www.brex.com/journal/press/new-cto", text=ANNOUNCEMENT)
    brief, _ = run({"people": [person("Jane Newleader", own)]})
    assert brief["people"][0]["current_basis"] == "official_source"
    assert brief["people"][0]["verified"] is False


def test_issuer_host_is_official_only_if_it_names_the_company():
    wire = web("https://www.businesswire.com/news/brex-cto", text=ANNOUNCEMENT)
    brief, _ = run({"people": [person("Jane Newleader", wire)]})
    assert brief["people"][0]["current_basis"] == "official_source"
    other = web("https://www.businesswire.com/news/acme", text="Acme announced Jane Newleader as CTO.")
    brief, _ = run({"people": [person("Jane Newleader", other)]})
    assert brief["people"] == []


def test_single_third_party_press_still_dropped():
    press = web("https://techcrunch.com/brex-cto", text=ANNOUNCEMENT)
    brief, report = run({"people": [person("Jane Newleader", press)]})
    assert brief["people"] == [] and "current role not confirmed" in report["dropped"][0]["reason"]


def test_parent_company_roles_count_as_current(stub):
    stub("server.apollo._get", lambda path, params: load("apollo_org_brex.json"))
    apollo.get_company("brex.com")
    resp = {"results": [{"url": "https://linkedin.com/in/cap1-ciso", "title": "Pat Approver", "entities": [
        {"type": "person", "properties": {"name": "Pat Approver", "workHistory": [
            {"title": "CISO", "company": {"name": "Capital One"}, "dates": {"from": "2023-01-01", "to": None}}]}}]}]}
    stub("server.exa._post", lambda path, body: resp)
    [pat] = exa.search_people("Capital One", ["CISO"])
    brief, _ = run({"people": [{**person("Pat Approver", pat["source_id"]), "title": "CISO",
                                "deal_role": "parent_org_approver"}]})
    assert brief["people"][0]["current_basis"] == "profile"


def test_undated_evidence_caps_strength_at_medium():
    undated = web("https://brex.com/blog/undated", published=None)
    brief, report = run({"signals": [{
        "headline": "h", "type": "migration", "horizon": "tactical", "use_case": "Migrations",
        "strength": "strong", "evidence": [{"source_id": undated, "quote": "migrating our monolith from Java 8"}]}]})
    assert brief["signals"][0]["strength"] == "medium"
    assert any("capped at medium" in f["flag"] for f in report["flags"])
