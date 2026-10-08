"""Brief schema + validator.

The model cites source_ids only. The validator resolves every citation against
the cache, verifies every quote, and drops anything that doesn't hold up,
reporting what it dropped and why. URLs and dates are filled in at render time
from the cache, never from model output.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .apollo import normalize_domain
from .cache import get_doc, site, source_id_for
from .dates import parse_date
from .exa import _history, employment_at
from .knowledge import match_customers
from .lint import NUMBER_WORDS, lint_message
from .verify import normalize, verify_quote

SIGNAL_MAX_MONTHS = {"tactical": 6, "strategic": 12}

# Hosts of company-issued documents (filings, press releases). Official only when
# the document names the company; the company's own domain is always official.
ISSUER_HOSTS = ("sec.gov", "businesswire.com", "prnewswire.com", "globenewswire.com")


# ---------- primitives ----------

class Evidence(BaseModel):
    source_id: str
    quote: str | None = None       # verbatim from the source; checked by verify_quote
    date_quote: str | None = None  # for undated sources: a verbatim byline/dateline on the same source
    date: str | None = None        # set by the validator: source metadata date, else the verified date_quote


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
    situation: Literal["copilot_autocomplete", "piloting_competitor", "competitor_deal_recent",
                       "competitor_in_production", "other"]


ChampionKind = Literal["hiring_manager", "public_pain", "ex_devin_customer", "works_on_signal",
                       "new_in_role", "ai_appetite"]
# The only checkable Devin-usage proxy: profile history × customers.json.
# hiring_manager / public_pain / works_on_signal stay as facts; they do not earn the badge.
DEVIN_USAGE = "ex_devin_customer"
# works_on_signal is kept as evidence only for someone senior enough to carry it internally.
SENIOR_TITLE = re.compile(r"\b(manager|lead|staff|principal|director|head)\b", re.I)
PEOPLE_MAX = 5


class ChampionEvidence(Fact):
    """Likely champion is only confirmed ex_devin_customer. Other kinds explain why they're listed."""
    kind: ChampionKind


DealRole = Literal["economic_buyer", "champion", "technical_evaluator", "gatekeeper", "parent_org_approver"]


class Person(BaseModel):
    name: str
    title: str
    deal_role: DealRole
    team: str | None = None           # as their own profile describes it
    why: str                          # agent's one-line assessment
    evidence: list[Evidence] = Field(min_length=1)
    champion_evidence: list[ChampionEvidence] = []
    hooks: list[Fact] = []
    reporting_line: Fact | None = None  # only when a source states it
    # Model judgment: they own a competitor rollout (Cursor, Claude Code, Copilot agent, in-house).
    # Must be backed in why/hooks. Not a champion — a displace/expand risk.
    incumbent_owner: bool = False
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


class Email(BaseModel):
    subject: str
    body: str


class Claim(BaseModel):
    """One prospect-specific statement in a draft, with the verbatim quote that backs it."""
    text: str
    source_id: str
    quote: str


class Draft(BaseModel):
    name: str                          # must be in people
    email: Email
    linkedin_note: str
    linkedin_followup: str
    built_from: list[Evidence] = Field(min_length=1)
    claims: list[Claim] = Field(min_length=1)  # every prospect-specific claim; each must verify
    notes: str = ""  # rep-only send notes; never part of a message
    # Set by the validator: no hook, champion evidence, or signal attached to this person.
    low_confidence: bool | None = None
    lint: list[dict] = []


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
    drafts: list[Draft] = []


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


# ---------- claim ↔ quote overlap ----------

# A verified quote can still fail to support its claim ("runs in a Bazel monorepo" cited
# to a quote about Cursor agents). So every specific term in a claim (capitalized words,
# numbers) must appear in its own quote. Deterministic and deliberately strict: a claim
# that over-blocks gets a better quote or is split; a semantic judge is out of scope here.
ABBREVIATIONS = [{"pr", "pull request"}, {"ci", "continuous integration"},
                 {"cd", "continuous delivery", "continuous deployment"}, {"qa", "quality assurance"},
                 {"sre", "site reliability"}, {"k8s", "kubernetes"}, {"ai", "artificial intelligence"},
                 {"ml", "machine learning"}, {"llm", "large language model"}, {"eng", "engineering"}]
_SENTENCES = re.compile(r"(?<=[.!?:;])\s+")
_WORD = re.compile(r"[A-Za-z0-9][\w'’.+#&-]*")


def _forms(term: str) -> set[str]:
    """The term, its singular ("PRs" → "pr"), and abbreviation equivalents, normalized."""
    t = normalize(term)
    forms = {t} | ({t[:-1]} if len(t) > 2 and t.endswith("s") else set())
    for group in ABBREVIATIONS:
        if forms & group:
            forms |= group
    return forms


def _in_quote(forms: set[str], quote: str) -> bool:
    return any(re.search(rf"(?<![\w]){re.escape(f)}(?:s|es|'s)?(?![\w])", quote) for f in forms)


def unsupported_terms(claim: str, quote: str, ignore: set[str]) -> list[str]:
    """Capitalized terms and numbers in `claim` that its `quote` doesn't contain.
    Ignored: names in `ignore` (prospect, contact, Devin) and sentence-initial words,
    unless their casing is a term's (PRs, BazelCon, CI)."""
    q = re.sub(r"(?<=\d),(?=\d)", "", normalize(quote))
    missing = []
    for sentence in _SENTENCES.split(re.sub(r"(?<=\d),(?=\d)", "", claim.strip())):
        for i, word in enumerate(_WORD.findall(sentence)):
            word = re.sub(r"['’]s$", "", word.rstrip(".'’"))
            for j, part in enumerate(p for p in word.split("-") if p):
                low = part.lower()
                if low in ignore:
                    continue
                if re.search(r"\d", part) and low not in {"k8s"}:
                    nums = re.findall(r"\d+(?:[.,]\d+)*", part)
                    if not all(re.search(rf"(?<![\d.]){re.escape(n.replace(',', ''))}(?!\d)", q) for n in nums):
                        missing.append(part)
                elif low in NUMBER_WORDS:
                    digits = NUMBER_WORDS[low]
                    if not (_in_quote({low}, q) or (digits and re.search(rf"(?<![\d.]){re.escape(digits)}(?!\d)", q))):
                        missing.append(part)
                elif any(c.isupper() for c in part):
                    if i == 0 and j == 0 and not any(c.isupper() for c in part[1:]):
                        continue  # capitalized only because it starts the sentence
                    if not _in_quote(_forms(part), q):
                        missing.append(part)
    return list(dict.fromkeys(missing))


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
        self.profile_titles: dict[str, set[str]] = {}  # name -> current titles on their profile

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
            d = self._date(ev, doc, p)
            ev.date = d.isoformat() if d else None
            if max_months is not None:
                if d is None:
                    self.flags.append({"path": p, "flag": "publication date unknown"})
                elif _months_between(d, self.today) > max_months:
                    self.drop(p, f"source dated {d} is older than {max_months} months")
                    continue
            kept.append(ev)
        return kept

    def _date(self, ev: Evidence, doc: dict, path: str) -> date | None:
        """Source metadata date; for undated sources, a verified date_quote that states one date."""
        if (d := _doc_date(doc)) or not ev.date_quote:
            return d
        check = verify_quote(ev.source_id, ev.date_quote)
        if not check["verified"]:
            self.flags.append({"path": f"{path}.date_quote", "flag": f"date_quote not verified: {check['reason']}"})
            return None
        if not (d := parse_date(ev.date_quote, self.today)):
            self.flags.append({"path": f"{path}.date_quote",
                               "flag": "date_quote doesn't state exactly one past date"})
        return d

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
        ex_customers = self._ex_customers(profiles)
        for i, c in enumerate(list(p.champion_evidence)):
            if c.kind == "ex_devin_customer" and not ex_customers:
                self.drop(f"{path}.champion_evidence[{i}]",
                          "ex_devin_customer not confirmed: no past employer in the profile matches customers.json")
                p.champion_evidence.remove(c)
        p.hooks = self.facts(p.hooks, f"{path}.hooks")
        if p.reporting_line:
            p.reporting_line = self.fact(p.reporting_line, f"{path}.reporting_line")
        p.verified = len(distinct) >= 2
        self.profile_titles[p.name] = profile_titles
        p.profile_source_id = profiles[0]["source_id"] if profiles else None
        return p

    def _ex_customers(self, profiles: list[dict]) -> list[dict]:
        own = {normalize(c) for c in self.companies if c}
        past = {h["company"] for d in profiles for h in _history(d["data"].get("person"))
                if h["company"] and normalize(h["company"]) not in own}
        return match_customers(sorted(past))

    def label(self, p: Person, path: str, strong_signal_people: set[str]) -> None:
        """Likely champion = confirmed ex_devin_customer only. We cannot prove they ran Devin,
        only that they worked at a Devin customer. An AI title is not a champion.
        works_on_signal stays as evidence only for someone in a strong signal's who_cares whose
        profile title is senior; otherwise it becomes a hook. It never earns the badge."""
        for i, c in enumerate(list(p.champion_evidence)):
            if c.kind != "works_on_signal":
                continue
            if p.name not in strong_signal_people:
                why = "not in a strong signal's who_cares"
            elif not any(SENIOR_TITLE.search(t) for t in self.profile_titles.get(p.name, ())):
                why = f"profile title {sorted(self.profile_titles.get(p.name, ())) or 'unknown'} isn't senior"
            else:
                continue
            self.flags.append({"path": f"{path}.champion_evidence[{i}]",
                               "flag": f"works_on_signal kept as a hook: {why}"})
            p.champion_evidence.remove(c)
            p.hooks.append(Fact(value=c.value, evidence=c.evidence))
        p.label = "likely_champion" if any(c.kind == DEVIN_USAGE for c in p.champion_evidence) else "candidate"

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
        if s.strength == "strong" and not any(e.date for e in s.evidence):
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


def _top_names(raw: dict) -> list[str]:
    names = []
    for item in raw.get("top_contacts") or []:
        if isinstance(item, dict) and (n := item.get("name")) and n not in names:
            names.append(n)
    return names


def _person_rank(p: Person, top: list[str], who: set[str], index: int) -> tuple:
    if p.name in top:
        return (0, top.index(p.name))
    if any(c.kind == DEVIN_USAGE for c in p.champion_evidence):
        return (1, index)
    if p.incumbent_owner or p.hooks or p.champion_evidence or p.name in who:
        return (2, index)
    return (3, index)


_GREETING = re.compile(r"^(?:hi|hey|hello|dear)?\s*[\w.'’-]+(?:\s[\w.'’-]+)?,?\s*", re.I)


def email_opening(body: str) -> tuple[str, ...]:
    """First 8 content-words after a greeting; used to flag copy-paste skeletons."""
    rest = _GREETING.sub("", (body or "").strip(), count=1)
    return tuple(w.lower() for w in re.findall(r"[A-Za-z0-9']+", rest)[:8])


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
    strong_people = {n for s in brief.signals if s.strength == "strong" for n in s.who_cares}
    for i, p in enumerate(brief.people):
        v.label(p, f"people[{i}]", strong_people)
    who = {n for s in brief.signals for n in s.who_cares}
    top = _top_names(raw)
    ranked = sorted(enumerate(brief.people), key=lambda ip: _person_rank(ip[1], top, who, ip[0]))
    keep = {i for i, _ in ranked[:PEOPLE_MAX]}
    for i, p in enumerate(brief.people):
        if i not in keep:
            v.drop(f"people[{i}]", f"people cap: kept {PEOPLE_MAX}, dropped {p.name}")
    brief.people = [p for i, p in enumerate(brief.people) if i in keep]
    names = {p.name for p in brief.people}
    for s in brief.signals:
        s.who_cares = [n for n in s.who_cares if n in names]
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

    attached = {n for s in brief.signals for n in s.who_cares}
    anchored = {p.name for p in brief.people if p.hooks or p.champion_evidence or p.name in attached}
    # Claim terms that never need to be in the quote: the prospect, and Devin itself.
    own_names = {"devin", "i", *normalize(brief.company_name).split(),
                 *(dom.split(".")[0] for dom in v.domains[:1])}

    for i, d in _parse_list(raw.get("drafts"), Draft, "drafts", v):
        path = f"drafts[{i}]"
        if d.name not in names:
            v.drop(path, f"{d.name!r} is not in the validated people list")
            continue
        d.built_from = v.evidence(d.built_from, f"{path}.built_from")
        if not d.built_from:
            v.drop(path, "draft cites no valid sources")
            continue
        failed = []
        ignore = own_names | set(normalize(d.name).split())
        for j, c in enumerate(d.claims):
            check = verify_quote(c.source_id, c.quote)
            if not check["verified"]:
                failed.append(f"claim[{j}] {c.text[:60]!r}: {check['reason']}")
            elif missing := unsupported_terms(c.text, c.quote, ignore):
                failed.append(f"claim[{j}] {c.text[:60]!r}: its quote doesn't contain {missing}")
        if failed:
            v.drop(path, "unverified claims: " + "; ".join(failed))
            continue
        ids = [e.source_id for e in d.built_from] + [c.source_id for c in d.claims]
        checks = {
            "email": lint_message("email", d.email.body, ids, subject=d.email.subject),
            "linkedin_note": lint_message("linkedin_note", d.linkedin_note, ids),
            "linkedin_followup": lint_message("linkedin_followup", d.linkedin_followup, ids),
        }
        blocking = [f"{ch}: {x['issue']}" for ch, r in checks.items() for x in r["issues"] if x["severity"] == "block"]
        if blocking:
            v.drop(path, "; ".join(blocking))
            continue
        d.lint = [{"channel": ch, **x} for ch, r in checks.items() for x in r["issues"]]
        d.low_confidence = d.name not in anchored
        brief.drafts.append(d)

    openings: dict[tuple[str, ...], str] = {}
    for d in brief.drafts:
        key = email_opening(d.email.body)
        if len(key) < 8:
            continue
        if key in openings:
            msg = f"same opening as {openings[key]}'s email"
            d.lint.append({"channel": "email", "severity": "fix", "issue": msg})
            v.flags.append({"path": "drafts", "flag": f"{d.name}: {msg}"})
        else:
            openings[key] = d.name

    missing = [t.name for t in brief.top_contacts if t.name not in {d.name for d in brief.drafts}]
    if missing:
        v.flags.append({"path": "drafts", "flag": f"top contacts without a draft: {missing}"})

    if brief.verdict and brief.verdict.verdict == "act_now":
        strong = any(s.strength == "strong" for s in brief.signals)
        reachable = any(p.current_basis for p in brief.people if p.name in anchored)
        piloting = any(c.situation == "piloting_competitor" for c in brief.competitive_context)
        in_prod = any(c.situation == "competitor_in_production" for c in brief.competitive_context)
        if not piloting and not (strong and reachable):
            v.flags.append({"path": "verdict", "flag": "act_now needs a strong signal + a reachable contact "
                                                      "(verified current with a hook or signal), or a competitor pilot"})
        elif in_prod and not piloting:
            v.flags.append({"path": "verdict", "flag": "a direct competitor in production defaults to Warm "
                                                      "(angle: displace or expand); act_now needs a reason beyond it"})

    report = {"dropped_count": len(v.dropped), "dropped": v.dropped, "flags": v.flags}
    return brief.model_dump(), report
