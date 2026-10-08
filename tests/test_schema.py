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
    exa.search_people("Brex", ["platform"])  # fills the cache, incl. former employees' profiles
    from server.cache import source_id_for
    return {r["title"]: source_id_for(r["url"]) for r in load("exa_people_brex.json")["results"]}


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
               champion_evidence=[{"kind": "public_pain", "value": "Leads platform",
                                   "evidence": [{"source_id": blog, "quote": "Phil Burrows leads platform"}]}]),
    ]})
    names = [p["name"] for p in brief["people"]]
    assert names == ["Phil Burrows", "Phil Burrows"]
    first, second = brief["people"]
    assert first["verified"] is False and first["label"] == "candidate"
    assert second["verified"] is True and second["label"] == "likely_champion"  # senior Head + public_pain quote
    assert first["featured"] is True and second["featured"] is True
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
    [pat] = exa.search_people("Capital One", ["CISO"])["people"]
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


def champ(kind, src, quote="Phil Burrows leads platform"):
    return {"kind": kind, "value": kind, "evidence": [{"source_id": src, "quote": quote}]}


def strong_signal(src, who):
    return {"headline": "Java 17 migration", "type": "migration", "horizon": "tactical", "use_case": "Migrations",
            "strength": "strong", "who_cares": who,
            "evidence": [{"source_id": src, "quote": "migrating our monolith from Java 8 to Java 17"}]}


def test_champion_label_needs_strong_evidence(stub):
    ids = profiles(stub)
    blog = web("https://brex.com/blog/fresh")
    phil = lambda *ev: person("Phil Burrows", ids["Phil Burrows"], champion_evidence=list(ev))
    label = lambda raw: run(raw)[0]["people"][0]["label"]
    assert label({"people": [phil(champ("new_in_role", blog), champ("ai_appetite", blog))]}) == "candidate"
    # Senior Head of Platform + hiring_manager or public_pain quote → likely champion.
    assert label({"people": [phil(champ("hiring_manager", blog))]}) == "likely_champion"
    assert label({"people": [phil(champ("public_pain", blog))]}) == "likely_champion"
    # No quote on public_pain: not the badge path, even if senior.
    assert label({"people": [phil({"kind": "public_pain", "value": "talked DX",
                                   "evidence": [{"source_id": blog}]})]}) == "candidate"
    # works_on_signal: supporting only. Not in who_cares → hook. Never the badge.
    brief, report = run({"people": [phil(champ("works_on_signal", blog))]})
    p = brief["people"][0]
    assert p["label"] == "candidate" and p["champion_evidence"] == [] and p["hooks"][0]["value"] == "works_on_signal"
    assert any("not in a strong signal" in f["flag"] for f in report["flags"])
    kept = run({"people": [phil(champ("works_on_signal", blog))],
                "signals": [strong_signal(blog, ["Phil Burrows"])]})[0]["people"][0]
    assert kept["label"] == "candidate" and kept["champion_evidence"][0]["kind"] == "works_on_signal"
    # Official-source IC (title isn't senior): hiring_manager does not badge.
    junior_page = web("https://www.brex.com/journal/press/junior",
                      text="Brex today announced that Pat Junior has joined as Software Engineer. "
                           "Pat Junior is the hiring manager for the backend role.")
    junior = {**person("Pat Junior", junior_page), "title": "Software Engineer",
              "champion_evidence": [champ("hiring_manager", junior_page, quote="Pat Junior is the hiring manager")]}
    assert run({"people": [junior]})[0]["people"][0]["label"] == "candidate"


def test_ex_devin_customer_must_match_profile_history(stub, tmp_path, monkeypatch):
    import json
    ids = profiles(stub)
    blog = web("https://brex.com/blog/fresh")
    kd = tmp_path / "k"; kd.mkdir()
    monkeypatch.setenv("KNOWLEDGE_DIR", str(kd))
    (kd / "customers.json").write_text(json.dumps({"customers": []}))
    raw = {"people": [person("Phil Burrows", ids["Phil Burrows"], champion_evidence=[champ("ex_devin_customer", blog)])]}
    brief, report = run(raw)
    assert brief["people"][0]["label"] == "candidate"
    assert "ex_devin_customer not confirmed" in report["dropped"][-1]["reason"]
    (kd / "customers.json").write_text(json.dumps({"customers": [{"name": "Meta", "public_reference": True, "proofs": []}]}))
    assert run(raw)[0]["people"][0]["label"] == "likely_champion"  # profile history: Engineering Leader at Meta


def test_date_quote_dates_an_undated_source():
    page = "Building agents: 5 lessons learned. By Graham Fuller, April 2026. We migrated our monolith from Java 8 to Java 17."
    undated = web("https://brex.com/journal/agents", text=page, published=None)
    # strategic (12 mo): "April 2026" resolves to Apr 1, past the 6-month tactical window by design
    sig = {**strong_signal(undated, []), "horizon": "strategic",
           "evidence": [{"source_id": undated, "date_quote": "By Graham Fuller, April 2026",
                                                       "quote": "migrated our monolith from Java 8 to Java 17"}]}
    brief, report = run({"signals": [sig]})
    assert brief["signals"][0]["strength"] == "strong" and brief["signals"][0]["evidence"][0]["date"] == "2026-04-01"
    assert not report["flags"]
    for bad, flag in (("By Graham Fuller, May 2026", "not verified"), ("Building agents: 5 lessons learned", "exactly one")):
        s2 = {**sig, "evidence": [{**sig["evidence"][0], "date_quote": bad}]}
        brief, report = run({"signals": [s2]})
        assert brief["signals"][0]["strength"] == "medium"
        assert any(flag in f["flag"] for f in report["flags"]), bad


def test_unverified_draft_claim_blocks_draft(stub):
    from tests.test_briefs import EMAIL, NOTE, FOLLOW  # noqa: F401  (shared fixtures)
    ids = profiles(stub)
    blog = web("https://brex.com/blog/fresh")
    draft = {"name": "Phil Burrows", "email": {"subject": "Your Java 17 migration", "body": EMAIL},
             "linkedin_note": NOTE, "linkedin_followup": FOLLOW, "built_from": [{"source_id": blog}],
             "claims": [{"text": "laptops kept sleeping", "source_id": blog, "quote": "local agent runs broke whenever a laptop slept"}]}
    brief, report = run({"people": [person("Phil Burrows", ids["Phil Burrows"])], "drafts": [draft]})
    assert brief["drafts"] == [] and "unverified claims" in report["dropped"][0]["reason"]


def test_competitor_in_production_defaults_to_warm():
    blog = web("https://brex.com/blog/fresh")
    ev = [{"source_id": blog, "quote": "migrating our monolith from Java 8 to Java 17"}]
    _, report = run({"verdict": {"verdict": "act_now", "why_now": "x", "based_on": ev},
                     "competitive_context": [{"situation": "competitor_in_production", "value": "Cursor in prod", "evidence": ev}]})
    assert any("defaults to Warm" in f["flag"] or "act_now needs" in f["flag"] for f in report["flags"])


def test_claim_terms_must_be_in_its_quote():
    from server.schema import unsupported_terms
    ig = {"brex", "devin", "sridhar", "mocherla"}
    q = "We built a pipeline of custom self-hosted AI agents in Cursor that are capable of writing code."
    assert unsupported_terms("Brex's Micronaut 4 agents run in a Bazel monorepo", q, ig) == ["Micronaut", "4", "Bazel"]
    assert unsupported_terms("Agents open PRs overnight", "Overnight, the pipeline completes integration tests.", ig) == ["PRs"]
    assert unsupported_terms("The pipeline is built on Cursor agents", q, ig) == []         # sentence-initial "The"
    assert unsupported_terms("Sridhar's team built AI agents in Cursor", q, ig) == []        # contact's own name
    assert unsupported_terms("Agents open PRs", "agents open pull requests nightly", ig) == []  # abbreviation map
    assert unsupported_terms("Their CI runs", "continuous integration runs", ig) == []
    assert unsupported_terms("They moved 1,800 services", "moved 1800 services", ig) == []
    assert unsupported_terms("They wrote eleven skills", "we wrote 11 skills", ig) == []
    assert unsupported_terms("They wrote twelve skills", "we wrote eleven skills", ig) == ["twelve"]
    assert unsupported_terms("BazelCon talk on Gradle", "Gradle-Backed Dependency Resolution", ig) == ["BazelCon"]


def test_people_cap_applies_to_cards_only(stub):
    ids = profiles(stub)
    team = web("https://www.brex.com/journal/press/team",
               text="Brex today announced that Jane One, Jane Two and Jane Three have joined.")
    why = lambda sid: {"text": "entry", "based_on": [{"source_id": sid}]}
    people = [
        person("Jane One", team), person("Jane Two", team), person("Jane Three", team),
        person("Feng Zhao", ids["Feng Zhao"]), person("Sonny Gupta", ids["Sonny Gupta"]),
        person("Phil Burrows", ids["Phil Burrows"]), person("James Reggio", ids["James Reggio"]),
    ]
    brief, report = run({
        "people": people,
        "top_contacts": [{"name": "James Reggio", "why": why(ids["James Reggio"])},
                         {"name": "Phil Burrows", "why": why(ids["Phil Burrows"])}],
    })
    names = [p["name"] for p in brief["people"]]
    assert len(names) == 7  # cap does not drop people
    assert not any("people cap" in d["reason"] for d in report["dropped"])
    featured = [p["name"] for p in brief["people"] if p["featured"]]
    assert len(featured) == 5
    assert "James Reggio" in featured and "Phil Burrows" in featured


def test_wider_table_is_verified_people_grouped_by_team(stub):
    from server.briefs import card_people, render_markdown, wider_people
    ids = profiles(stub)
    blog = web("https://brex.com/blog/fresh")
    press = web("https://www.brex.com/journal/press/team",
                text="Brex today announced that Jane One, Jane Two and Jane Three have joined.")
    brief, _ = run({"people": [
        {**person("Phil Burrows", ids["Phil Burrows"], blog), "team": "Platform"},
        {**person("James Reggio", ids["James Reggio"], blog, press), "team": "Engineering leadership"},
        {**person("Jane One", press), "team": "Platform"},
    ]})
    assert [p["name"] for p in wider_people(brief["people"])] == ["James Reggio", "Phil Burrows"]
    md = render_markdown({"brief": brief, "date": "2026-10-07", "domain": "brex.com",
                          "report": {"dropped_count": 0, "flags": []}})
    who, _, rest = md.partition("## Wider buying center")
    assert "**Phil Burrows**" in who and "Jane One" in who  # Jane can still be a card
    assert "Phil Burrows" in rest and "James Reggio" in rest
    assert "Jane One" not in rest.split("## Sources")[0]  # not verified
    assert "| Platform |" in rest
    assert len(card_people(brief["people"])) == 3


def test_claim_not_supported_by_its_quote_blocks_draft(stub):
    from tests.test_briefs import EMAIL, NOTE, FOLLOW
    ids = profiles(stub)
    blog = web("https://brex.com/blog/fresh")
    draft = {"name": "Phil Burrows", "email": {"subject": "Your Java 17 migration", "body": EMAIL},
             "linkedin_note": NOTE, "linkedin_followup": FOLLOW, "built_from": [{"source_id": blog}],
             "claims": [{"text": "Brex is moving its monolith to Kotlin", "source_id": blog,
                         "quote": "migrating our monolith from Java 8 to Java 17"}]}
    brief, report = run({"people": [person("Phil Burrows", ids["Phil Burrows"])], "drafts": [draft]})
    assert brief["drafts"] == [] and "doesn't contain ['Kotlin']" in report["dropped"][0]["reason"]
