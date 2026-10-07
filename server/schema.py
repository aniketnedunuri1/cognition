"""Brief schema + validator.

The model cites source_ids only. The validator resolves every citation against
the cache, verifies every quote, and drops anything that doesn't hold up,
reporting what it dropped and why. URLs and dates are filled in at render time
from the cache, never from model output.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .apollo import normalize_domain
from .cache import get_doc, site, source_id_for
from .exa import employment_at
from .verify import normalize, verify_quote

SIGNAL_MAX_MONTHS = {"tactical": 6, "strategic": 12}

# Hosts of company-issued documents (filings, press releases). Official only when
# the document names the company; the company's own domain is always official.
ISSUER_HOSTS = ("sec.gov", "businesswire.com", "prnewswire.com", "globenewswire.com")


# ---------- primitives ----------

class Evidence(BaseModel):
    source_id: str
    quote: str | None = None  # verbatim from the source; checked by verify_quote


class Fact(BaseModel):
    """A sourced statement, written as the source states it ("Apollo reports 1,800")."""
    value: str
    evidence: list[Evidence] = Field(min_length=1)


class Assessment(BaseModel):
    """The agent's judgment — labeled as such — and the evidence it rests on."""
    text: str
    based_on: list[Evidence] = Field(min_length=1)


# ---------- sections ----------

class Snapshot(BaseModel):
    """Each field is a list: empty = Not found; several = cross-checked or conflicting."""
    hq: list[Fact] = []
    founded: list[Fact] = []
    industry: list[Fact] = []
    ownership: list[Fact] = []         # public / private / subsidiary
    parent_company: list[Fact] = []
    headcount: list[Fact] = []
    headcount_growth: list[Fact] = []
    revenue: list[Fact] = []
    funding: list[Fact] = []
    sells_to: list[Fact] = []
    products: list[Fact] = []


class Engineering(BaseModel):
    eng_headcount: list[Fact] = []
    open_eng_roles: list[Fact] = []
    tech_stack: list[Fact] = []
    ai_dev_tools: list[Fact] = []
    eng_blog_and_github: list[Fact] = []
    ai_posture: list[Fact] = []
    priorities: list[Fact] = []
    peers: list[Fact] = []


SignalType = Literal["migration", "tech_debt", "quality_security", "ai_mandate",
                     "leadership_change", "hiring_composition", "layoffs_ai"]


class Signal(BaseModel):
    headline: str
    type: SignalType
    horizon: Literal["tactical", "strategic"]
    evidence: list[Evidence] = Field(min_length=1)  # every item needs a quote
    use_case: str
    who_cares: list[str] = []  # names from people
    strength: Literal["strong", "medium"]


class CompetitiveItem(Fact):
    situation: Literal["copilot_autocomplete", "piloting_competitor", "competitor_deal_recent", "other"]


DealRole = Literal["economic_buyer", "champion", "technical_evaluator", "gatekeeper", "parent_org_approver"]


class Person(BaseModel):
    name: str
    title: str
    deal_role: DealRole
    team: str | None = None           # as their own profile describes it
    why: str                          # agent's one-line assessment
    evidence: list[Evidence] = Field(min_length=1)
    champion_evidence: list[Fact] = []
    hooks: list[Fact] = []
    reporting_line: Fact | None = None  # only when a source states it
    # Set by the validator, never by the model:
    verified: bool | None = None
    label: Literal["likely_champion", "candidate"] | None = None
    profile_source_id: str | None = None
    current_basis: Literal["profile", "official_source", "two_sources"] | None = None


class TopContact(BaseModel):
    name: str
    why: Assessment


class Verdict(BaseModel):
    verdict: Literal["act_now", "warm", "not_now"]
    why_now: str
    based_on: list[Evidence] = []
    watch_for: str | None = None


class Brief(BaseModel):
    domain: str
    company_name: str
    verdict: Verdict | None = None
    snapshot: Snapshot = Snapshot()
    engineering: Engineering = Engineering()
    signals: list[Signal] = []
    competitive_context: list[CompetitiveItem] = []
    people: list[Person] = []
    top_contacts: list[TopContact] = []
    abm_themes: list[Assessment] = []


# ---------- validator ----------

def _doc_date(doc: dict) -> date | None:
    try:
        return date.fromisoformat((doc.get("published_date") or "")[:10])
    except ValueError:
        return None


def _mentions(doc: dict, name: str) -> bool:
    hay = " ".join([doc.get("title") or "", doc.get("text") or "", *doc.get("highlights", [])])
    return normalize(name) in normalize(hay)


def _months_between(d: date, today: date) -> float:
    return (today - d).days / 30.44


class _Validator:
    def __init__(self, company_name: str, domain: str, today: date, use_cases: set[str] | None):
        # The account plus its parent (from the cached Apollo record, not model output):
        # parent-org approvers are current at the parent, and its site is official too.
        self.companies = [company_name]
        self.domains = [normalize_domain(domain)] if domain else []
        org = get_doc(source_id_for(f"apollo:org:{normalize_domain(domain)}")) if domain else None
        parent = ((org or {}).get("data") or {}).get("parent_company") or {}
        if parent.get("name"):
            self.companies.append(parent["name"])
        if parent.get("website"):
            self.domains.append(normalize_domain(parent["website"]))
        self.today = today
        self.use_cases = {u.lower() for u in use_cases} if use_cases else None
        self.dropped: list[dict] = []
        self.flags: list[dict] = []

    def drop(self, path: str, reason: str) -> None:
        self.dropped.append({"path": path, "reason": reason})

    # -- primitives --

    def evidence(self, items: list[Evidence], path: str, *, require_quote=False,
                 max_months: int | None = None) -> list[Evidence]:
        kept = []
        for i, ev in enumerate(items):
            p = f"{path}[{i}]"
            doc = get_doc(ev.source_id)
            if not doc:
                self.drop(p, f"source_id {ev.source_id!r} not in cache")
                continue
            if require_quote and not ev.quote:
                self.drop(p, "signal evidence needs an exact quote")
                continue
            if ev.quote:
                check = verify_quote(ev.source_id, ev.quote)
                if not check["verified"]:
                    self.drop(p, f"quote failed verification: {check['reason']}")
                    continue
            if max_months is not None:
                d = _doc_date(doc)
                if d is None:
                    self.flags.append({"path": p, "flag": "publication date unknown"})
                elif _months_between(d, self.today) > max_months:
                    self.drop(p, f"source dated {d} is older than {max_months} months")
                    continue
            kept.append(ev)
        return kept

    def fact(self, f: Fact, path: str) -> Fact | None:
        f.evidence = self.evidence(f.evidence, f"{path}.evidence")
        if not f.evidence:
            self.drop(path, "no valid evidence left")
            return None
        return f

    def facts(self, items: list[Fact], path: str) -> list[Fact]:
        return [f for i, x in enumerate(items) if (f := self.fact(x, f"{path}[{i}]"))]

    def assessment(self, a: Assessment, path: str) -> Assessment | None:
        a.based_on = self.evidence(a.based_on, f"{path}.based_on")
        if not a.based_on:
            self.drop(path, "assessment cites no valid evidence")
            return None
        return a

    # -- sections --

    def person(self, p: Person, path: str) -> Person | None:
        p.evidence = self.evidence(p.evidence, f"{path}.evidence")
        docs = [get_doc(e.source_id) for e in p.evidence]
        if not docs:
            self.drop(path, "no valid evidence left")
            return None

        named = [d for d in docs if _mentions(d, p.name)]
        if not named:
            self.drop(path, f"name {p.name!r} not found in any cited source")
            return None

        distinct = {d["source_id"] for d in docs}
        profiles = [d for d in docs if d["kind"] == "person"]
        roles = [employment_at(d["data"].get("person"), c) for d in profiles for c in self.companies]
        profile_titles = {r["current_title"] for r in roles if r["is_current"] is True}
        if profile_titles and normalize(p.title) not in {normalize(t) for t in profile_titles}:
            self.flags.append({"path": f"{path}.title",
                               "flag": f"title differs from profile's current title {sorted(profile_titles)}"})

        # Current if: the profile says so; or the company itself names them (new
        # leaders often haven't updated LinkedIn); or two independent sources do.
        if profile_titles:
            p.current_basis = "profile"
        elif any(self._official(d) for d in named):
            p.current_basis = "official_source"
        elif len(distinct) >= 2:
            p.current_basis = "two_sources"
        else:
            self.drop(path, "current role not confirmed: no current profile, no official company source, "
                            "and no second source")
            return None
        if profiles and not profile_titles:
            self.flags.append({"path": path, "flag": f"profile doesn't list a current role here; "
                                                    f"listed on {p.current_basis}"})

        p.champion_evidence = self.facts(p.champion_evidence, f"{path}.champion_evidence")
        p.hooks = self.facts(p.hooks, f"{path}.hooks")
        if p.reporting_line:
            p.reporting_line = self.fact(p.reporting_line, f"{path}.reporting_line")
        p.verified = len(distinct) >= 2
        p.label = "likely_champion" if p.champion_evidence else "candidate"
        p.profile_source_id = profiles[0]["source_id"] if profiles else None
        return p

    def _official(self, doc: dict) -> bool:
        host = site(doc.get("url")) or ""
        if any(host == d or host.endswith("." + d) for d in self.domains):
            return True
        if any(host == h or host.endswith("." + h) for h in ISSUER_HOSTS):
            return any(_mentions(doc, c) for c in self.companies if c)
        return False

    def signal(self, s: Signal, path: str, people: set[str]) -> Signal | None:
        s.evidence = self.evidence(s.evidence, f"{path}.evidence", require_quote=True,
                                   max_months=SIGNAL_MAX_MONTHS[s.horizon])
        if not s.evidence:
            self.drop(path, "no valid evidence left")
            return None
        if s.strength == "strong" and not any(_doc_date(get_doc(e.source_id)) for e in s.evidence):
            s.strength = "medium"
            self.flags.append({"path": f"{path}.strength", "flag": "capped at medium: no dated evidence"})
        if self.use_cases is not None and s.use_case.lower() not in self.use_cases:
            self.drop(path, f"use case {s.use_case!r} is not in knowledge/use_cases")
            return None
        unknown = [n for n in s.who_cares if n not in people]
        if unknown:
            self.flags.append({"path": f"{path}.who_cares", "flag": f"removed names not in people: {unknown}"})
            s.who_cares = [n for n in s.who_cares if n in people]
        return s


def _parse_list(raw: list, model, path: str, v: _Validator) -> list:
    out = []
    for i, item in enumerate(raw or []):
        try:
            out.append((i, model.model_validate(item)))
        except ValidationError as e:
            v.drop(f"{path}[{i}]", f"schema: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")
    return out


def validate_brief(raw: dict, *, today: date | None = None,
                   use_cases: set[str] | None = None) -> tuple[dict, dict]:
    """Return (clean brief, report). Never raises on bad content: drops and reports."""
    v = _Validator(raw.get("company_name") or "", raw.get("domain") or "", today or date.today(), use_cases)
    brief = Brief(domain=raw.get("domain", ""), company_name=raw.get("company_name", ""))

    for section, model in (("snapshot", Snapshot), ("engineering", Engineering)):
        fields = {}
        for name in model.model_fields:
            items = _parse_list((raw.get(section) or {}).get(name), Fact, f"{section}.{name}", v)
            fields[name] = [f for i, x in items if (f := v.fact(x, f"{section}.{name}[{i}]"))]
        setattr(brief, section, model(**fields))

    brief.people = [p for i, x in _parse_list(raw.get("people"), Person, "people", v)
                    if (p := v.person(x, f"people[{i}]"))]
    names = {p.name for p in brief.people}

    brief.signals = [s for i, x in _parse_list(raw.get("signals"), Signal, "signals", v)
                     if (s := v.signal(x, f"signals[{i}]", names))]
    brief.competitive_context = [c for i, x in _parse_list(raw.get("competitive_context"), CompetitiveItem,
                                                           "competitive_context", v)
                                 if (c := v.fact(x, f"competitive_context[{i}]"))]

    for i, t in _parse_list(raw.get("top_contacts"), TopContact, "top_contacts", v):
        path = f"top_contacts[{i}]"
        if t.name not in names:
            v.drop(path, f"{t.name!r} is not in the validated people list")
        elif (why := v.assessment(t.why, f"{path}.why")):
            t.why = why
            brief.top_contacts.append(t)
    brief.top_contacts = brief.top_contacts[:3]

    brief.abm_themes = [a for i, x in _parse_list(raw.get("abm_themes"), Assessment, "abm_themes", v)
                        if (a := v.assessment(x, f"abm_themes[{i}]"))]

    if raw.get("verdict"):
        try:
            verdict = Verdict.model_validate(raw["verdict"])
            verdict.based_on = v.evidence(verdict.based_on, "verdict.based_on")
            if verdict.verdict != "not_now" and not verdict.based_on:
                v.drop("verdict", f"{verdict.verdict} cites no valid evidence")
            else:
                brief.verdict = verdict
        except ValidationError as e:
            v.drop("verdict", f"schema: {e.errors()[0]['msg']}")

    report = {"dropped_count": len(v.dropped), "dropped": v.dropped, "flags": v.flags}
    return brief.model_dump(), report
