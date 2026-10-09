"""Sumble: inferred org charts — decision-makers + their inferred direct reports.

Reporting lines are Sumble's *inference*, never confirmed. Renders label them
as such; `confidence` (0-1) on a direct report is Sumble's own score.

Credit model: charged per row returned (1 base + 1/paid attribute + 1/related
person). A full chart is ~50-100 credits per account — call once per account.
TTL is "lookup" (7d) everywhere so a re-run doesn't re-spend.
"""

from __future__ import annotations

import os
import re
import time

import httpx

from .apollo import AI_CODING_TOOLS, normalize_domain
from .cache import cached_call, make_doc, now_iso, upsert_doc
from .exa import ProviderError

API = "https://api.sumble.com/v9"
MAX_DMS = 5
MAX_PER_ACCOUNT = 25
MAX_REPORTS_PER_DM = 5
MAX_ANCHORS = 5
MIN_CONFIDENCE = 0.4
LEADERS_LIMIT = 40

# Titles that can't be on an eng buying committee.
IRRELEVANT = re.compile(
    r"sales|marketing|financ|accounting|legal|people|talent|recruit|hr\b|"
    r"revenue|customer|support|success|operations|design|product\b|growth|"
    r"communications|counsel|compliance|facilities|\boffice\b|credit|risk|"
    r"\bgtm\b|banking|commercial|deal desk",
    re.I,
)
# Corporate execs who aren't an eng buying-committee member at larger orgs.
# At small companies (<SMALL_ORG employees) the CEO/founder often *is* the buyer.
NON_BUYER_EXEC = re.compile(r"president|founder|chief executive|\bceo\b|\bcco\b|\bcoo\b", re.I)
SMALL_ORG = 200
BUYER = re.compile(
    r"chief technology|cto\b|chief ai|cio\b|chief information|"
    r"(svp|vp|vice president).*(eng|tech|software|platform|infra|ai)|"
    r"eng.*(svp|vp|vice president)",
    re.I,
)
PLATFORM = re.compile(
    r"(head|director|senior director|manager).*(platform|developer|devprod|"
    r"devex|productivity|infra|eng|ai|tools|enablement)|"
    r"(platform|developer|devprod|devex|productivity|infra|ai).*(head|director)",
    re.I,
)
GATEKEEPER = re.compile(r"ciso|security|procurement", re.I)

LEVEL_RANK = {
    "CXO": 15, "SVP": 14, "VP": 13, "Executive Director": 12,
    "Senior Director": 11, "Director": 10, "General Manager": 9, "Head": 8,
    "Associate Director": 7, "Senior Manager": 6, "Manager": 5,
    "Principal": 4, "Lead": 3, "Senior": 2, "Individual Contributor": 1,
}


def _post(path: str, body: dict) -> dict:
    key = os.environ.get("SUMBLE_API_KEY")
    if not key:
        raise ProviderError("SUMBLE_API_KEY is not set")
    r = httpx.post(API + path, json=body, timeout=60,
                   headers={"Authorization": f"Bearer {key}"})
    if r.status_code >= 400:
        raise ProviderError(f"Sumble {path} returned {r.status_code}: {r.text[:300]}")
    return r.json()


def _get(path: str) -> dict:
    key = os.environ.get("SUMBLE_API_KEY")
    if not key:
        raise ProviderError("SUMBLE_API_KEY is not set")
    r = httpx.get(API + path, timeout=60, headers={"Authorization": f"Bearer {key}"})
    if r.status_code >= 400:
        raise ProviderError(f"Sumble {path} returned {r.status_code}: {r.text[:300]}")
    return r.json()


def _poll_people(body: dict) -> dict:
    """POST /people is async: start, then poll request_id until terminal."""
    res = _post("/people", body)
    for _ in range(60):
        if res.get("status") not in ("pending", "running"):
            return res
        time.sleep(2)
        res = _post("/people", {"request_id": res["request_id"]})
    raise ProviderError("Sumble people request did not finish in time")


def _no_docs(response: dict, fetched_at: str) -> list[dict]:
    return []


def _call(endpoint: str, request: dict, fetch) -> dict:
    _, response = cached_call(endpoint, request, "lookup", fetch, _no_docs)
    return response


def resolve_org(domain: str) -> dict | None:
    body = {"organizations": [{"url": domain}],
            "select": {"attributes": ["id", "name", "employee_count", "industry"]}}
    res = _call("sumble/organizations", body, lambda: _post("/organizations", body))
    orgs = res.get("organizations") or []
    return orgs[0] if orgs else None


def senior_leaders(org_id: int) -> tuple[list[dict], int]:
    body = {
        "filter": {
            "organization_ids": [org_id],
            "query": {"query": "job_function EQ 'Engineering & R&D' "
                               "AND job_level_min EQ 'Head'"},
        },
        "select": {"attributes": ["name", "job_title", "job_level",
                                  "linkedin_url", "location"]},
        "order_by_column": "job_level",
        "limit": LEADERS_LIMIT,
    }
    res = _call("sumble/people", body, lambda: _poll_people(body))
    return res.get("people") or [], res.get("total") or 0


def related(person_ids: list[int]) -> list[dict]:
    body = {
        "people": [{"person_id": pid} for pid in person_ids],
        "select": {
            "related_people": {
                "direction": ["managers", "direct_reports"],
                "attributes": ["name", "job_title", "job_level",
                               "linkedin_url", "confidence"],
            },
        },
    }
    res = _call("sumble/people", body, lambda: _poll_people(body))
    return res.get("people") or []


def is_eng_relevant(title: str, *, allow_execs: bool = False) -> bool:
    if not title or IRRELEVANT.search(title):
        return False
    if not allow_execs and NON_BUYER_EXEC.search(title):
        return False
    return True


def deal_role(title: str) -> str:
    if GATEKEEPER.search(title):
        return "gatekeeper"
    if BUYER.search(title) or NON_BUYER_EXEC.search(title):
        return "economic_buyer"
    if PLATFORM.search(title):
        return "technical_evaluator"
    return "influencer"


def why_line(title: str, role: str) -> str:
    if role == "economic_buyer":
        return "Likely economic buyer — owns engineering budget and tooling decisions."
    if role == "technical_evaluator":
        return "Technical evaluator — owns the platform/dev-productivity surface Devin lands on."
    if role == "gatekeeper":
        return "Gatekeeper — will review security/procurement. Named, not a first touch."
    return "Senior eng leader — likely in the room for a tooling decision."


def pick_decision_makers(people: list[dict], *, small_org: bool = False) -> list[dict]:
    """Dedupe by (name, title); rank buyers first, then evaluators, by seniority."""
    seen, cands = set(), []
    for p in people:
        a = p.get("attributes") or {}
        key = (a.get("name"), a.get("job_title"))
        if not a.get("name") or not a.get("job_title") or key in seen:
            continue
        seen.add(key)
        if not is_eng_relevant(a["job_title"], allow_execs=small_org):
            continue
        role = deal_role(a["job_title"])
        cands.append({
            "person_id": p["person_id"],
            "name": a["name"],
            "title": a["job_title"],
            "level": a.get("job_level"),
            "linkedin": a.get("linkedin_url"),
            "location": a.get("location"),
            "role": role,
            "rank": LEVEL_RANK.get(a.get("job_level") or "", 0),
        })
    order = {"economic_buyer": 0, "technical_evaluator": 1, "influencer": 2, "gatekeeper": 3}
    cands.sort(key=lambda c: (order[c["role"]], -c["rank"]))
    picked = cands[:MAX_DMS]
    if picked and not any(c["role"] == "economic_buyer" for c in picked):
        for c in cands:
            if c["role"] == "economic_buyer":
                picked[-1] = c
                break
    return picked


def person_name(row: dict) -> str | None:
    return (row.get("attributes") or {}).get("name")


def _norm_linkedin(url: str | None) -> str | None:
    """linkedin.com/in/<slug> lowercase, no scheme/www/trailing slash — the join key."""
    if not url:
        return None
    u = url.strip().lower().split("?")[0].rstrip("/")
    u = re.sub(r"^https?://(www\.)?", "", u)
    return u if "linkedin.com/" in u else None


def _norm_name(name: str | None) -> str:
    return re.sub(r"[^a-z ]", "", (name or "").lower()).strip()


def match_anchors(leaders: list[dict], people: list[dict]) -> tuple[list[dict], list[str]]:
    """Join sourced people onto Sumble leaders: LinkedIn URL first, then a unique
    exact-name match (a generic name only joins when it maps to one leader).
    Returns (leader rows in `people` order with `matched` set, unmatched names)."""
    by_li = {}
    by_name: dict[str, list[dict]] = {}
    for row in leaders:
        a = row.get("attributes") or {}
        if li := _norm_linkedin(a.get("linkedin_url")):
            by_li.setdefault(li, row)
        if n := _norm_name(a.get("name")):
            by_name.setdefault(n, []).append(row)
    anchors, unmatched = [], []
    for p in people:
        row = None
        if li := _norm_linkedin(p.get("linkedin_url") or p.get("linkedin")):
            row = by_li.get(li)
        if row is None:
            cands = by_name.get(_norm_name(p.get("name"))) or []
            if len(cands) == 1:
                row = cands[0]
        if row is None:
            unmatched.append(p.get("name") or "?")
            continue
        if any(a["person_id"] == row["person_id"] for a in anchors):
            continue
        anchors.append(row)
    return anchors, unmatched


def _anchor(row: dict) -> dict:
    """A Sumble leader row normalized to the chart's DM shape."""
    a = row.get("attributes") or {}
    title = a.get("job_title") or ""
    return {
        "person_id": row["person_id"],
        "name": a.get("name"),
        "title": title,
        "level": a.get("job_level"),
        "linkedin": a.get("linkedin_url"),
        "location": a.get("location"),
        "role": deal_role(title),
        "rank": LEVEL_RANK.get(a.get("job_level") or "", 0),
        "matched": True,
    }


def build_chart(domain: str, people: list[dict] | None = None) -> dict:
    org = resolve_org(domain)
    if not org or not (org.get("attributes") or {}).get("id"):
        return {"domain": domain, "error": "organization not found in Sumble"}
    oa = org["attributes"]
    leaders, total = senior_leaders(oa["id"])
    dms, unmatched = [], []
    if people:
        rows, unmatched = match_anchors(leaders, people)
        dms = [_anchor(r) for r in rows[:MAX_ANCHORS]]
    if not dms:
        small_org = bool(oa.get("employee_count")) and oa["employee_count"] < SMALL_ORG
        dms = pick_decision_makers(leaders, small_org=small_org)
        for d in dms:
            d["matched"] = False
    seen_reports: set[str] = set()
    for dm, row in zip(dms, related([d["person_id"] for d in dms])):
        rel = row.get("related_people") or {}
        mgrs = [m for m in rel.get("managers") or [] if person_name(m)]
        dm["manager"] = mgrs[0] if mgrs else None
        seen_names = {dm["name"]} | seen_reports
        reports = []
        for r in rel.get("direct_reports") or []:
            ra = r.get("attributes") or {}
            nm = ra.get("name")
            if not nm or not ra.get("job_title") or nm in seen_names:
                continue
            if not is_eng_relevant(ra["job_title"]):
                continue
            score = ((r.get("confidence") or {}).get("score")) or 0
            if score < MIN_CONFIDENCE:
                continue
            seen_names.add(nm)
            seen_reports.add(nm)
            reports.append({
                "name": nm,
                "title": ra["job_title"],
                "level": ra.get("job_level"),
                "linkedin": ra.get("linkedin_url"),
                "confidence": score,
                "rank": LEVEL_RANK.get(ra.get("job_level") or "", 0),
            })
        reports.sort(key=lambda r: (-r["confidence"], -r["rank"]))
        dm["reports"] = reports[:MAX_REPORTS_PER_DM]
    # Cap total rows (DMs + reports) at MAX_PER_ACCOUNT, trimming longest report lists.
    while sum(1 + len(d["reports"]) for d in dms) > MAX_PER_ACCOUNT:
        longest = max(dms, key=lambda d: len(d["reports"]))
        longest["reports"].pop()
        if not any(d["reports"] for d in dms):
            break
    return {
        "domain": domain,
        "name": oa.get("name") or domain,
        "employee_count": oa.get("employee_count"),
        "industry": oa.get("industry"),
        "leaders_found": total,
        "leaders_considered": min(total, LEADERS_LIMIT),
        "unmatched_people": unmatched,
        "decision_makers": dms,
    }


def get_org_chart(domain: str, people: list[dict] | None = None) -> dict:
    domain = normalize_domain(domain)
    chart = build_chart(domain, people)
    # One citable doc per account: the brief cites this source_id and the render
    # reads the chart back from the cache — never from model output.
    doc = upsert_doc(make_doc(
        identity=f"sumble:org_chart:{domain}", kind="sumble", label="Sumble",
        title=f"Sumble org chart (inferred): {chart.get('name') or domain}",
        data=chart, retrieved_at=now_iso()))
    return {"attribution": "Sumble (inferred reporting lines)", "source_id": doc["source_id"], **chart}


# ---------- tech stack ----------

def get_tech_stack(domain: str) -> dict:
    """Confirmed-used tech stack: technologies Sumble has verified are in use via
    job posts (job_post_used_count = confirming posts, not mentions). Different
    from Apollo's website-detected tags and from any-mention search matches."""
    domain = normalize_domain(domain)
    org = resolve_org(domain)
    if not org or not (org.get("attributes") or {}).get("id"):
        return {"domain": domain, "found": False, "error": "organization not found in Sumble"}
    org_id = org["attributes"]["id"]
    req = {"organization_id": org_id}
    res = _call(f"sumble/organizations/{org_id}/techs", req,
                lambda: _get(f"/organizations/{org_id}/techs"))

    functions = []
    names = []
    for fn in res.get("business_functions") or []:
        cats = []
        for cat in fn.get("categories") or []:
            techs = [{"name": t["name"], "slug": t.get("slug"),
                      "confirming_job_posts": t.get("job_post_used_count")}
                     for t in cat.get("technologies") or [] if t.get("name")]
            if not techs:
                continue
            names += [t["name"] for t in techs]
            cats.append({"category": cat.get("category_name"), "technologies": techs,
                         "also_confirmed": [e["name"] for e in cat.get("evidence") or []
                                            if e.get("name") and e["name"] not in {t["name"] for t in techs}]})
        if cats:
            functions.append({"business_function": fn.get("business_function"), "categories": cats})

    data = {"domain": domain, "name": (org["attributes"].get("name")) or domain,
            "business_functions": functions,
            "ai_coding_tools": sorted({n for n in names if AI_CODING_TOOLS.search(n)})}
    doc = upsert_doc(make_doc(
        identity=f"sumble:techs:{domain}", kind="sumble", label="Sumble",
        title=f"Sumble confirmed tech stack: {data['name']}", data=data,
        retrieved_at=now_iso()))
    return {"found": True, "attribution": "Sumble (confirmed-used via job posts)",
            "source_id": doc["source_id"], **data}
