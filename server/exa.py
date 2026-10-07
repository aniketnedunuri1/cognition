"""Exa: web search, page fetch, people search.

Request shapes follow the build-with-exa skill: query + type auto + highlights,
nothing else unless the task requires it.
"""

from __future__ import annotations

import os
import re
from datetime import date

import httpx

from .cache import cached_call, get_doc, make_doc, site

EXA_URL = "https://api.exa.ai"
EXCERPT_CHARS = 6000


class ProviderError(Exception):
    pass


def _post(path: str, body: dict) -> dict:
    key = os.environ.get("EXA_API_KEY")
    if not key:
        raise ProviderError("EXA_API_KEY is not set")
    r = httpx.post(EXA_URL + path, json=body, headers={"x-api-key": key}, timeout=90)
    if r.status_code != 200:
        raise ProviderError(f"Exa {path} returned {r.status_code}: {r.text[:300]}")
    return r.json()


# ---------- search_web ----------

def _web_docs(response: dict, fetched_at: str) -> list[dict]:
    return [
        make_doc(
            identity=r["url"], kind="web", url=r["url"], title=r.get("title"),
            published_date=r.get("publishedDate"), highlights=r.get("highlights"),
            retrieved_at=fetched_at,
        )
        for r in response.get("results", [])
    ]


def search_web(query: str, since: str | None = None) -> list[dict]:
    body: dict = {"query": query, "type": "auto", "contents": {"highlights": True}}
    if since:
        # Hard publication cutoff: only for the signal bar's 6/12-month windows.
        body["startPublishedDate"] = date.fromisoformat(since).isoformat()
    docs, _ = cached_call("exa/search", body, "discovery", lambda: _post("/search", body), _web_docs)
    return [
        {
            "source_id": d["source_id"],
            "title": d["title"],
            "site": site(d["url"]),
            "published_date": d["published_date"],
            "highlights": d["highlights"],
        }
        for d in docs
    ]


# ---------- fetch_page ----------

def _fetch_contents(body: dict) -> dict:
    response = _post("/contents", body)
    # /contents returns 200 even when a URL fails: check statuses, and raise so
    # the failure isn't cached as an empty page.
    failed = [s for s in response.get("statuses", []) if s.get("status") != "success"]
    if failed or not response.get("results"):
        reason = failed[0].get("error", failed[0]) if failed else "no content returned"
        raise ProviderError(f"Page not fetched: {reason}")
    return response


def _page_docs(response: dict, fetched_at: str) -> list[dict]:
    return [
        make_doc(
            identity=r["url"], kind="web", url=r["url"], title=r.get("title"),
            published_date=r.get("publishedDate"), text=r.get("text"),
            retrieved_at=fetched_at,
        )
        for r in response["results"][:1]
    ]


def fetch_page(url_or_source_id: str, offset: int = 0) -> dict:
    """Full text is cached (for verify_quote); the model gets a capped excerpt."""
    if url_or_source_id.startswith("src_"):
        doc = get_doc(url_or_source_id)
        if not doc or not doc.get("url"):
            return {"status": "not_fetched", "reason": "unknown source_id or source has no URL"}
        url = doc["url"]
    else:
        url = url_or_source_id

    body = {"urls": [url], "text": True}
    try:
        [doc], _ = cached_call("exa/contents", body, "lookup", lambda: _fetch_contents(body), _page_docs)
    except ProviderError as e:
        return {"status": "not_fetched", "url": url, "reason": str(e)}

    text = doc["text"] or ""
    end = offset + EXCERPT_CHARS
    return {
        "status": "ok",
        "source_id": doc["source_id"],
        "title": doc["title"],
        "site": site(doc["url"]),
        "published_date": doc["published_date"],
        "excerpt": text[offset:end],
        "total_chars": len(text),
        "next_offset": end if end < len(text) else None,
    }


# ---------- search_people ----------

_SUFFIXES = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "plc", "gmbh"}


def _norm_company(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


def _month_start(s: str | None) -> date | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-\d{2})?", s or "")
    return date(int(m[1]), int(m[2]), 1) if m else None


def employment_at(entity_props: dict | None, company: str, today: date | None = None) -> dict:
    """Current-role status at `company` from Exa's structured workHistory.

    Never guesses: if the structure is missing or ambiguous, is_current is
    "unknown" and the person needs a second source before being listed as current.
    """
    today = today or date.today()
    unknown = {"is_current": "unknown", "current_title": None, "start_date": "unknown",
               "months_in_role": "unknown"}
    history = (entity_props or {}).get("workHistory")
    if not history:
        return {**unknown, "reason": "no structured work history in profile"}

    target = _norm_company(company)
    exact, variants = [], []
    for h in history:
        name = _norm_company((h.get("company") or {}).get("name") or "")
        if not name:
            continue
        if name == target:
            exact.append(h)
        elif name.startswith(target + " ") or target.startswith(name + " "):
            variants.append(h)

    current = [h for h in exact if (h.get("dates") or {}).get("to") is None]
    if current:
        latest = max(current, key=lambda h: (h.get("dates") or {}).get("from") or "")
        start = _month_start((latest.get("dates") or {}).get("from"))
        months = (today.year - start.year) * 12 + today.month - start.month if start else "unknown"
        return {"is_current": True, "current_title": latest.get("title"),
                "start_date": start.isoformat()[:7] if start else "unknown",
                "months_in_role": months, "reason": "profile lists a current role at this company"}
    if variants:
        names = sorted({h["company"]["name"] for h in variants})
        return {**unknown, "reason": f"profile lists a similar company name: {names}"}
    if exact:
        return {**unknown, "is_current": False, "reason": "profile lists this company as a past employer"}
    return {**unknown, "is_current": False, "reason": "company not in profile work history"}


def _person_entity(result: dict) -> dict | None:
    for e in result.get("entities") or []:
        if e.get("type") == "person":
            return e.get("properties") or {}
    return None


def _people_docs(response: dict, fetched_at: str) -> list[dict]:
    docs = []
    for r in response.get("results", []):
        props = _person_entity(r)
        docs.append(make_doc(
            identity=r["url"], kind="person", url=r["url"],
            title=(props or {}).get("name") or r.get("title"),
            published_date=r.get("publishedDate"), highlights=r.get("highlights"),
            data={"person": props} if props else {}, retrieved_at=fetched_at,
        ))
    return docs


def search_people(company: str, roles: list[str]) -> list[dict]:
    """One people search per role, merged by profile."""
    by_id: dict[str, dict] = {}
    for role in roles:
        body = {"query": f"{role} at {company}", "type": "auto", "category": "people",
                "contents": {"highlights": True}}
        docs, _ = cached_call("exa/search", body, "discovery", lambda b=body: _post("/search", b), _people_docs)
        for d in docs:
            if d["source_id"] in by_id:
                by_id[d["source_id"]]["matched_roles"].append(role)
                continue
            props = (d["data"] or {}).get("person")
            history = [
                {"company": (h.get("company") or {}).get("name"), "title": h.get("title"),
                 "from": (h.get("dates") or {}).get("from"), "to": (h.get("dates") or {}).get("to")}
                for h in (props or {}).get("workHistory") or []
            ]
            by_id[d["source_id"]] = {
                "source_id": d["source_id"],
                "name": d["title"],
                **employment_at(props, company),
                "location": (props or {}).get("location"),
                "work_history": history,
                "highlights": d["highlights"],
                "matched_roles": [role],
            }
    return list(by_id.values())
