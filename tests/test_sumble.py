from server import sumble
from server.schema import validate_brief

ORG = {"organizations": [{"attributes": {"id": 7, "name": "Acme",
                                         "employee_count": 500, "industry": "Fintech"}}]}
LEADERS = {"status": "succeeded", "total": 2, "people": [
    {"person_id": 1, "attributes": {"name": "Amy Cto", "job_title": "CTO",
                                    "job_level": "CXO", "linkedin_url": "https://linkedin.com/in/amy",
                                    "location": "NYC"}},
    {"person_id": 2, "attributes": {"name": "Bob Dir", "job_title": "Director of Platform",
                                    "job_level": "Director",
                                    "linkedin_url": "https://linkedin.com/in/bob"}},
    {"person_id": 3, "attributes": {"name": "Cara Sales", "job_title": "VP Sales",
                                    "job_level": "VP"}},
]}
RELATED = {"status": "succeeded", "people": [
    {"person_id": 1, "related_people": {"managers": [], "direct_reports": [
        {"attributes": {"name": "Dan Rep", "job_title": "Head of DevEx",
                        "job_level": "Head", "linkedin_url": "https://linkedin.com/in/dan"},
         "confidence": {"score": 0.8}},
        {"attributes": {"name": "Eve Low", "job_title": "Engineer",
                        "job_level": "IC"},
         "confidence": {"score": 0.2}},
    ]}},
    {"person_id": 2, "related_people": {"managers": [
        {"attributes": {"name": "Amy Cto", "job_title": "CTO"}}],
        "direct_reports": []}},
]}


def fake_post(path, body):
    if path == "/organizations":
        return ORG
    if "request_id" in body:
        raise AssertionError("should not poll a succeeded response")
    if "filter" in body:
        return LEADERS
    return RELATED


def test_org_chart_builds_and_cites(monkeypatch, stub):
    stub("server.sumble._post", fake_post)
    out = sumble.get_org_chart("acme.com")
    assert out["source_id"].startswith("src_")
    dms = out["decision_makers"]
    assert [d["name"] for d in dms] == ["Amy Cto", "Bob Dir"]  # VP Sales filtered out
    assert dms[0]["role"] == "economic_buyer"
    assert [r["name"] for r in dms[0]["reports"]] == ["Dan Rep"]  # 0.2 confidence dropped
    assert dms[1]["manager"]["attributes"]["name"] == "Amy Cto"

    clean, report = validate_brief(
        {"domain": "acme.com", "company_name": "Acme", "org_chart": out["source_id"]})
    assert clean["org_chart"] == out["source_id"]
    assert not any(d["path"] == "org_chart" for d in report["dropped"])


def test_org_chart_requires_cached_doc():
    clean, report = validate_brief(
        {"domain": "acme.com", "company_name": "Acme", "org_chart": "src_0000000000"})
    assert clean["org_chart"] is None
    assert any(d["path"] == "org_chart" for d in report["dropped"])
