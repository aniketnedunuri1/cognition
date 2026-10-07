"""Apollo (Free plan): org enrichment and job postings.

Every value is Apollo's report and is cited as "Apollo", never restated as ours.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import defaultdict
from urllib.parse import urlsplit

import httpx

from .cache import cached_call, make_doc, site
from .exa import ProviderError

APOLLO_URL = "https://api.apollo.io/api/v1"
MAX_JOB_PAGES = 5  # 100 postings per page

AI_CODING_TOOLS = re.compile(
    r"cursor|copilot|codeium|windsurf|tabnine|sourcegraph|\bcody\b|codewhisperer|amazon q|"
    r"replit|devin|cognition|codex|claude code|gitlab duo|jetbrains ai|augment code", re.I)
AI_PLATFORMS = re.compile(r"anthropic|claude|openai|chatgpt|gemini|mistral|cohere|bedrock|azure openai", re.I)


def _get(path: str, params: dict) -> dict:
    key = os.environ.get("APOLLO_API_KEY")
    if not key:
        raise ProviderError("APOLLO_API_KEY is not set")
    r = httpx.get(APOLLO_URL + path, params=params, headers={"x-api-key": key}, timeout=60)
    if r.status_code != 200:
        raise ProviderError(f"Apollo {path} returned {r.status_code}: {r.text[:300]}")
    return r.json()


def normalize_domain(domain: str) -> str:
    d = domain.strip().lower()
    if "://" in d:
        d = urlsplit(d).netloc
    return d.split("/")[0].removeprefix("www.")


def _pct(x) -> str | None:
    return f"{x * 100:+.1f}%" if isinstance(x, (int, float)) else None


# ---------- get_company ----------

def _org_docs(domain: str):
    def to_docs(response: dict, fetched_at: str) -> list[dict]:
        org = response.get("organization")
        if not org:
            return []
        parent = org.get("owned_by_organization") or None
        techs = sorted({t["name"] for t in org.get("current_technologies") or [] if t.get("name")})
        data = {
            "name": org.get("name"),
            "apollo_org_id": org.get("id"),
            "domain": org.get("primary_domain"),
            "website": org.get("website_url"),
            "linkedin": org.get("linkedin_url"),
            "description": org.get("short_description"),
            "hq": ", ".join(x for x in (org.get("city"), org.get("state"), org.get("country")) if x) or None,
            "founded_year": org.get("founded_year"),
            "industry": org.get("industry"),
            "publicly_traded": (
                f"{org.get('publicly_traded_exchange') or ''}:{org['publicly_traded_symbol']}".lstrip(":")
                if org.get("publicly_traded_symbol") else None
            ),
            "parent_company": (
                {"name": parent.get("name"), "website": parent.get("website_url")} if parent else None
            ),
            "employees": org.get("estimated_num_employees"),
            "headcount_growth": {
                "6mo": _pct(org.get("organization_headcount_six_month_growth")),
                "12mo": _pct(org.get("organization_headcount_twelve_month_growth")),
                "24mo": _pct(org.get("organization_headcount_twenty_four_month_growth")),
            },
            "department_headcount": org.get("departmental_head_count") or {},
            "annual_revenue": org.get("annual_revenue_printed"),
            "total_funding": org.get("total_funding_printed"),
            "latest_funding_stage": org.get("latest_funding_stage"),
            "latest_funding_date": (org.get("latest_funding_round_date") or "")[:10] or None,
            "funding_events": [
                {"date": (f.get("date") or "")[:10], "type": f.get("type"), "amount": f.get("amount"),
                 "currency": f.get("currency"), "investors": f.get("investors"), "news_url": f.get("news_url")}
                for f in org.get("funding_events") or []
            ],
            "ai_coding_tools": [t for t in techs if AI_CODING_TOOLS.search(t)],
            "ai_platforms": [t for t in techs if AI_PLATFORMS.search(t) and not AI_CODING_TOOLS.search(t)],
            "technologies": techs,
        }
        return [make_doc(identity=f"apollo:org:{domain}", kind="apollo_org", label="Apollo",
                         title=f"Apollo organization record: {data['name']}", data=data,
                         retrieved_at=fetched_at)]
    return to_docs


def _org_doc(domain: str) -> dict | None:
    domain = normalize_domain(domain)
    params = {"domain": domain}
    docs, _ = cached_call("apollo/organizations/enrich", params, "lookup",
                       lambda: _get("/organizations/enrich", params), _org_docs(domain))
    return docs[0] if docs else None


def get_company(domain: str) -> dict:
    doc = _org_doc(domain)
    if not doc:
        return {"found": False, "domain": normalize_domain(domain), "reason": "Apollo has no organization for this domain"}
    return {"found": True, "source_id": doc["source_id"], "attribution": "Apollo",
            "retrieved_at": doc["retrieved_at"], **doc["data"]}


# ---------- get_jobs ----------

_ENG = re.compile(
    r"engineer|engineering|developer|software|\bsre\b|site reliability|devops|platform|infrastructure|"
    r"\binfra\b|\bqa\b|quality|\btest|sdet|security|architect|machine learning|\bml\b|mobile|"
    r"\bios\b|android|front[- ]?end|back[- ]?end|full[- ]?stack|technical program|\bcto\b|data platform",
    re.I)
_NOT_ENG = re.compile(
    r"sales|solutions|support|customer|account|recruit|marketing|partner|developer relations|"
    r"field engineer|implementation", re.I)


def is_eng_relevant(title: str) -> bool:
    return bool(_ENG.search(title)) and not _NOT_ENG.search(title)


def _fold(s: str) -> str:
    """Lowercase and strip accents, one char per char (indexes stay aligned)."""
    return "".join(unicodedata.normalize("NFKD", ch)[0] for ch in unicodedata.normalize("NFC", s)).lower()


def clean_title(title: str, cities: set[str], states: set[str]) -> str:
    """Apollo appends locations to titles: "EM, Bill Pay New Seattle, Washington, ...",
    "SWE, Full Stack Seattle Seattle". A posting's own city field doesn't always
    match its title, so cut at the earliest city seen anywhere in the response that
    is followed by a comma, another known place, or the end. Accent-insensitive (titles
    say São Paulo, fields say Sao Paulo). Then drop the trailing "New" marker."""
    title = unicodedata.normalize("NFC", title or "")
    folded = _fold(title)
    places = "|".join(re.escape(_fold(x)) for x in sorted(cities | states) if x)
    cut = len(title)
    for city in filter(None, cities):
        # city, then: a comma, another place ("Seattle Seattle", "New York New York"), or the end
        after = rf"(?=,|\s+(?:{places})\b|$)" if places else r"(?=,|$)"
        if (m := re.search(rf"\s{re.escape(_fold(city))}{after}", folded)) and m.start() > 0:
            cut = min(cut, m.start())
    return re.sub(r"(\s+New)?[\s,;-]*$", "", title[:cut]).strip()


def _job_docs(response: dict, fetched_at: str) -> list[dict]:
    jobs = [j for j in response.get("organization_job_postings") or [] if j.get("url")]
    cities = {j.get("city") for j in jobs} - {None}
    states = {j.get("state") for j in jobs} - {None}
    docs = []
    for j in jobs:
        location = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x) or None
        docs.append(make_doc(
            identity=j["url"], kind="job", url=j["url"], title=clean_title(j.get("title"), cities, states),
            published_date=j.get("posted_at"), retrieved_at=fetched_at,
            data={"raw_title": j.get("title"), "location": location,
                  "posted_at": j.get("posted_at"), "last_seen_at": j.get("last_seen_at")},
        ))
    return docs


def get_jobs(domain: str, eng_only: bool = True) -> dict:
    org = _org_doc(domain)
    if not org or not org["data"].get("apollo_org_id"):
        return {"found": False, "domain": normalize_domain(domain), "reason": "Apollo has no organization for this domain"}
    org_id = org["data"]["apollo_org_id"]

    docs, truncated = [], False
    path = f"/organizations/{org_id}/job_postings"
    for page in range(1, MAX_JOB_PAGES + 1):
        params = {"page": page, "per_page": 100}
        page_docs, response = cached_call(f"apollo{path}", params, "discovery",
                                          lambda p=params: _get(path, p), _job_docs)
        docs += page_docs
        total_pages = (response.get("pagination") or {}).get("total_pages") or 1
        if page >= total_pages:
            break
    else:
        truncated = True

    groups: dict[str, list[dict]] = defaultdict(list)
    for d in {d["source_id"]: d for d in docs}.values():
        groups[d["title"]].append(d)

    titles = []
    for title, ds in groups.items():
        eng = is_eng_relevant(title)
        if eng_only and not eng:
            continue
        ds.sort(key=lambda d: d["published_date"] or "", reverse=True)
        titles.append({
            "title": title,
            "eng_relevant": eng,
            "latest_posted": (ds[0]["published_date"] or "")[:10] or None,
            "postings": [{"source_id": d["source_id"], "location": d["data"]["location"],
                          "posted": (d["published_date"] or "")[:10] or None, "site": site(d["url"])}
                         for d in ds],
        })
    titles.sort(key=lambda t: t["latest_posted"] or "", reverse=True)
    titles.sort(key=lambda t: not t["eng_relevant"])  # stable: eng first, newest first
    return {
        "found": True,
        "attribution": "Apollo",
        "total_postings": len({d["source_id"] for d in docs}),
        "truncated": truncated,  # Apollo has more than MAX_JOB_PAGES pages
        "unique_titles": len(groups),
        "eng_relevant_titles": sum(is_eng_relevant(t) for t in groups),
        "titles": titles,
    }
