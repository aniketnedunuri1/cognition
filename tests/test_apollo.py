from conftest import load
from server import apollo
from server.cache import get_doc


def stub_apollo(stub):
    def fake(path, params):
        if path == "/organizations/enrich":
            return load("apollo_org_brex.json")
        jobs = load("apollo_jobs_brex.json")
        jobs["pagination"]["total_pages"] = 1
        return jobs
    return stub("server.apollo._get", fake)


def test_company_is_one_attributed_document(stub):
    stub_apollo(stub)
    c = apollo.get_company("https://www.brex.com/")
    assert c["found"] and c["attribution"] == "Apollo"
    assert c["parent_company"]["name"] == "Capital One"
    assert c["employees"] == 1800 and c["annual_revenue"] == "700M"
    assert c["headcount_growth"]["12mo"] == "+13.3%"
    assert c["department_headcount"]["engineering"] == 356
    assert c["ai_coding_tools"] == ["Cursor"]
    assert "Anthropic Claude" in c["ai_platforms"]
    assert get_doc(c["source_id"])["label"] == "Apollo"


def test_unknown_domain_is_not_found(stub):
    stub("server.apollo._get", lambda path, params: {})
    assert apollo.get_company("nope.example")["found"] is False


def test_clean_title_strips_locations():
    cities = {"Seattle", "New York", "Sao Paulo", "San Francisco"}
    states = {"Washington", "New York", "State of Sao Paulo", "California"}
    cases = {
        "Engineering Manager, Bill Pay New Seattle, Washington, United States": "Engineering Manager, Bill Pay",
        "Brand Marketing Lead New New York, New York, United States; San Francisco, California, United States":
            "Brand Marketing Lead",
        "Partnerships Manager, Accounting Channel New York, New York, United States":
            "Partnerships Manager, Accounting Channel",
        "Senior Software Engineer, Full Stack New York New York": "Senior Software Engineer, Full Stack",
        "Data Engineer New São Paulo, São Paulo, Brazil": "Data Engineer",
        "Staff Product Manager, Card New Seattle, Washington, United States": "Staff Product Manager, Card",
        "Senior Software Engineer, Full Stack Seattle Seattle": "Senior Software Engineer, Full Stack",
        "Senior Software Engineer, Full Stack São Paulo São Paulo": "Senior Software Engineer, Full Stack",
        "ERM Compliance Lead": "ERM Compliance Lead",
        "New Business Account Executive": "New Business Account Executive",
    }
    for raw, want in cases.items():
        assert apollo.clean_title(raw, cities, states) == want


def test_eng_relevance():
    assert apollo.is_eng_relevant("Engineering Manager, Bill Pay")
    assert apollo.is_eng_relevant("Senior Software Engineer, Platform")
    assert not apollo.is_eng_relevant("Developer Support Specialist")
    assert not apollo.is_eng_relevant("Solutions Engineer")
    assert not apollo.is_eng_relevant("ERM Compliance Lead")
    assert not apollo.is_eng_relevant("Staff Product Manager, AI")
    assert apollo.is_eng_relevant("AI Engineer, Product")


def test_jobs_dedupe_by_title_keep_every_posting(stub):
    calls = stub_apollo(stub)
    jobs = apollo.get_jobs("brex.com", eng_only=False)
    assert jobs["total_postings"] == 100 and not jobs["truncated"]
    em = next(t for t in jobs["titles"] if t["title"] == "Engineering Manager, Bill Pay")
    assert len(em["postings"]) == 4 and em["eng_relevant"]
    assert len({p["source_id"] for p in em["postings"]}) == 4
    assert jobs["titles"][0]["eng_relevant"]  # engineering first
    apollo.get_jobs("brex.com")
    assert len(calls) == 2  # enrich + one jobs page; second call fully cached


def test_jobs_follow_pagination(stub):
    pages = []
    def fake(path, params):
        if path == "/organizations/enrich":
            return load("apollo_org_brex.json")
        pages.append(params["page"])
        return {"organization_job_postings": [], "pagination": {"total_pages": 3}}
    stub("server.apollo._get", fake)
    apollo.get_jobs("brex.com")
    assert pages == [1, 2, 3]
