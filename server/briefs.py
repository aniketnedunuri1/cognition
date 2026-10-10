"""Brief persistence, "since last run" diff, and renders (Markdown, .docx, Slack text).

briefs/<domain>/<YYYY-MM-DD>.json is the source of truth; the .md, .docx and Slack
summary are renders of it. Every URL and date in a render comes from the cache, never
the model. In HTTP mode the .docx is served at an unguessable per-brief link.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
from datetime import date, datetime
from pathlib import Path

from .apollo import normalize_domain
from .cache import ROOT, get_doc, now_iso
from .knowledge import use_case_names
from .schema import validate_brief
from .verify import normalize

VERDICT = {"act_now": "Act now", "warm": "Warm", "not_now": "Not now"}
ROLE = {"economic_buyer": "Economic buyer",
        "technical_evaluator": "Technical evaluator", "gatekeeper": "Gatekeeper",
        "parent_org_approver": "Parent-company approver"}
LOW_CONFIDENCE = "Low-confidence draft: no personal signal or hook found."
KIND = {"hiring_manager": "hiring manager", "public_pain": "public talk about the pain",
        "ex_devin_customer": "ex-Devin customer", "works_on_signal": "works on a tier-1 signal",
        "new_in_role": "new in role", "ai_appetite": "AI appetite"}
SOURCE_LABELS = {"job_post": "job post", "leader_hire": "leader hire", "eng_blog": "eng blog",
                 "exec_statement": "exec statement", "news_press": "news/press",
                 "social_post": "social post", "profile": "profile data", "other": "other"}
ADD_LABELS = {"hiring_alert": "Hiring alert", "tech_mention": "Tech mention",
              "headcount_growth": "Headcount growth", "financial_event": "Financial event",
              "news_mention": "News mention", "other": "Signal"}
# Why someone was badged, in rep words (ex-Devin customer is the strongest reason).
BADGE_REASON = {"ex_devin_customer": "ex-Devin customer", "hiring_manager": "hiring this work",
                "public_pain": "talks publicly about the pain"}


def champion_reason(p: dict) -> str:
    for k in BADGE_REASON:
        if any(c["kind"] == k for c in p.get("champion_evidence") or []):
            return BADGE_REASON[k]
    return ""


def sort_signals(signals: list[dict]) -> list[dict]:
    """Tier first; inside a tier, newest dated evidence first."""
    by_newest = sorted(signals, reverse=True,
                       key=lambda s: max((e.get("date") or "" for e in s.get("evidence") or []), default=""))
    return sorted(by_newest, key=lambda s: s["tier"])
# Single-valued quantities where disagreeing sources get flagged (the playbook's 25% threshold).
QUANTITATIVE = ("headcount", "revenue", "funding")
CONFLICT_RATIO = 1.25
# Public base URL of the HTTP server (set by `server.app --http`). None: the CLI, which
# shows the local file path instead of a link.
BASE_URL: str | None = None


def bdir() -> Path:
    return Path(os.environ.get("BRIEFS_DIR", ROOT / "briefs"))


def _saved(domain: str) -> list[Path]:
    return sorted(p for p in (bdir() / normalize_domain(domain)).glob("*.json")
                  if not p.name.endswith(".comments.json"))


def _tokens_path() -> Path:
    return bdir() / ".tokens.json"


def _load_tokens() -> dict:
    p = _tokens_path()
    return json.loads(p.read_text()) if p.exists() else {}


# ---------- diff ----------

def diff_briefs(old: dict, new: dict) -> dict:
    def sig_keys(b):
        return {normalize(s["headline"]): {e["source_id"] for e in s["evidence"]} for s in b["signals"]}
    old_sigs, new_sigs = sig_keys(old), sig_keys(new)
    old_ev = set().union(*old_sigs.values()) if old_sigs else set()
    new_signals = [s["headline"] for s in new["signals"]
                   if normalize(s["headline"]) not in old_sigs
                   and not ({e["source_id"] for e in s["evidence"]} & old_ev)]
    old_people, new_people = {p["name"] for p in old["people"]}, {p["name"] for p in new["people"]}
    ov, nv = (old.get("verdict") or {}).get("verdict"), (new.get("verdict") or {}).get("verdict")
    out = {
        "new_signals": new_signals,
        "new_contacts": sorted(new_people - old_people),
        "departed_contacts": sorted(old_people - new_people),
        "verdict_change": f"{VERDICT.get(ov, 'none')} → {VERDICT.get(nv, 'none')}" if ov != nv else None,
    }
    out["nothing_new"] = not any(out.values())
    return out


# ---------- save / load ----------

def save_brief(domain: str, brief: dict, rep: str | None = None) -> dict:
    """Validate, diff against the last run, and save. Returns the validator report,
    the diff, and the Slack summary. Fix dropped items with real evidence, then save again.
    `rep` scopes the file to <date>.<rep>.json: two reps can run the same account the
    same day without overwriting each other; a re-save by the same rep updates in place."""
    from .feedback import rep_slug
    domain = normalize_domain(domain)
    slug = rep_slug(rep)
    clean, report = validate_brief({**brief, "domain": domain}, use_cases=use_case_names())
    today = date.today().isoformat()
    stem = f"{today}.{slug}" if slug else today
    previous = [p for p in _saved(domain) if p.stem != stem]
    prev = json.loads(previous[-1].read_text()) if previous else None
    since = {"since": prev["date"], **diff_briefs(prev["brief"], clean)} if prev else None

    out = bdir() / domain / f"{stem}.json"
    # Download tokens live in a gitignored sidecar so committed briefs carry no secrets.
    tokens = _load_tokens()
    token = tokens.get(f"{domain}/{stem}") or secrets.token_urlsafe(16)
    tokens[f"{domain}/{stem}"] = token
    _tokens_path().parent.mkdir(parents=True, exist_ok=True)
    _tokens_path().write_text(json.dumps(tokens, indent=1))
    record = {"domain": domain, "date": today, "stem": stem, "saved_at": now_iso(), "brief": clean,
              "report": report, "since_last_run": since, "download_token": token}
    if slug:
        record["requested_by"] = slug
    out.parent.mkdir(parents=True, exist_ok=True)
    disk = {k: v for k, v in record.items() if k != "download_token"}
    out.write_text(json.dumps(disk, indent=1, ensure_ascii=False))
    markdown = render_markdown(record)
    out.with_suffix(".md").write_text(markdown)
    from .docx_render import brief_to_docx
    brief_to_docx(record, out.with_suffix(".docx"))
    from .html_render import render_html
    docx_href = (f"/briefs/{token}/{domain}-{stem}.docx" if BASE_URL else f"{stem}.docx")
    out.with_suffix(".html").write_text(render_html(record, markdown, docx_href))
    return {"saved": _rel(out), "report": report, "since_last_run": since,
            "brief_markdown": markdown, "slack_summary": render_slack(record)}


def _stem(rec: dict) -> str:
    """Filename stem for a record; pre-rep briefs have no `stem` key."""
    return rec.get("stem") or rec["date"]


def brief_link(rec: dict) -> str:
    """The .docx download link in HTTP mode; the local path otherwise."""
    stem = _stem(rec)
    if BASE_URL and rec.get("download_token"):
        return f"{BASE_URL.rstrip('/')}/briefs/{rec['download_token']}/{rec['domain']}-{stem}.docx"
    return _rel(bdir() / rec["domain"] / f"{stem}.docx")


def _find_by_token(token: str) -> Path | None:
    """The brief record path (sans suffix) for a download token, else None."""
    if not token:
        return None
    for key, tok in _load_tokens().items():
        if hmac.compare_digest(tok, token):
            path = bdir() / key
            # str + suffix: stems can carry a ".<rep>" suffix that with_suffix() would eat
            return path if Path(str(path) + ".json").exists() else None
    return None


def find_download(token: str) -> Path | None:
    """The .docx for a download token, if any brief has it."""
    stem = _find_by_token(token)
    if not stem:
        return None
    docx = Path(str(stem) + ".docx")
    return docx if docx.exists() else None


def report_link(rec: dict) -> str:
    """The annotatable report URL in HTTP mode; the local .html path otherwise."""
    if BASE_URL and rec.get("download_token"):
        return f"{BASE_URL.rstrip('/')}/report/{rec['download_token']}"
    return _rel(bdir() / rec["domain"] / f"{_stem(rec)}.html")


def find_report(token: str) -> Path | None:
    stem = _find_by_token(token)
    if not stem:
        return None
    page = Path(str(stem) + ".html")
    return page if page.exists() else None


# ---------- rep feedback on the HTML report ----------

def _comments_file(stem: Path) -> Path:
    return stem.parent / f"{stem.name}.comments.json"


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(path)


def save_comments(token: str, comments: list[dict], rep: str | None = None) -> dict | None:
    """Append submitted report comments to the brief's <stem>.comments.json.
    Each comment is tagged with a rep: the explicit one (per-comment or form-level),
    else the brief's requested_by. Returns {domain, date, pending} or None."""
    from .feedback import rep_slug
    stem = _find_by_token(token)
    if not stem:
        return None
    try:
        owner = json.loads(Path(str(stem) + ".json").read_text()).get("requested_by")
    except (OSError, json.JSONDecodeError):
        owner = None
    default_rep = rep_slug(rep) or owner
    path = _comments_file(stem)
    data = json.loads(path.read_text()) if path.exists() else {"comments": []}
    start = len(data["comments"])
    for i, c in enumerate(comments):
        entry = {"id": f"c{start + i + 1}", "processed": False, "submitted_at": now_iso(),
                 "section": str(c.get("section") or "")[:200],
                 "quote": str(c.get("quote") or "")[:4000],
                 "note": str(c.get("note") or "")[:2000],
                 "tag": str(c.get("tag") or "")[:100]}
        if rep_slug(c.get("rep")) or default_rep:
            entry["rep"] = rep_slug(c.get("rep")) or default_rep
        data["comments"].append(entry)
    _write_json_atomic(path, data)
    pending = sum(1 for c in data["comments"] if not c["processed"])
    return {"domain": stem.parent.name, "date": stem.name, "saved": _rel(path),
            "added": len(comments), "pending": pending}


def get_brief_feedback(domain: str) -> dict:
    """Unprocessed report comments for a domain, oldest brief first."""
    domain = normalize_domain(domain)
    pending = []
    for path in sorted((bdir() / domain).glob("*.comments.json")):
        data = json.loads(path.read_text())
        pending += [{"brief_date": path.name.removesuffix(".comments.json"), **c}
                    for c in data["comments"] if not c["processed"]]
    return {"found": bool(pending), "domain": domain, "comments": pending}


def mark_brief_feedback_processed(domain: str, comment_ids: list[str]) -> dict:
    """Mark report comments processed after they've been distilled (or judged
    non-rule feedback). Returns the ids actually marked."""
    domain = normalize_domain(domain)
    ids, marked = set(comment_ids), []
    for path in sorted((bdir() / domain).glob("*.comments.json")):
        data = json.loads(path.read_text())
        changed = False
        for c in data["comments"]:
            if c["id"] in ids and not c["processed"]:
                c["processed"] = True
                c["processed_at"] = now_iso()
                marked.append(c["id"])
                changed = True
        if changed:
            _write_json_atomic(path, data)
    return {"marked": marked, "unknown_ids": sorted(ids - set(marked))}


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def _migrate(brief: dict) -> dict:
    """Upgrade older saved briefs to the current schema so they can be merged and
    re-saved. Signals: strength strong/medium → tier 1/2, missing source → other."""
    for s in brief.get("signals") or []:
        if "tier" not in s:
            s["tier"] = 1 if s.pop("strength", None) == "strong" else 2
            s.pop("strength", None)
        s.setdefault("source", "other")
    for p in brief.get("people") or []:
        if p.get("deal_role") == "champion":  # the seat is folded into technical_evaluator
            p["deal_role"] = "technical_evaluator"
        if p.get("label") == "likely_champion":
            p["label"] = "champion"
    return brief


def get_last_brief(domain: str) -> dict:
    files = _saved(domain)
    if not files:
        return {"found": False, "domain": normalize_domain(domain)}
    rec = json.loads(files[-1].read_text())
    return {"found": True, "date": rec["date"], "brief": _migrate(rec["brief"])}


def load_record(domain: str, day: str | None = None) -> tuple[Path, dict] | None:
    files = [p for p in _saved(domain) if day is None or p.stem == day or p.stem.startswith(day + ".")]
    if not files:
        return None
    rec = json.loads(files[-1].read_text())
    rec["download_token"] = _load_tokens().get(f"{rec['domain']}/{_stem(rec)}")
    return files[-1], rec


# ---------- renders ----------

class _Sources:
    """Numbers each cited document once; renders [n] markers and the source list."""

    def __init__(self):
        self.order: list[str] = []

    def cite(self, evidence: list[dict]) -> str:
        ns = []
        for e in evidence:
            if e["source_id"] not in self.order:
                self.order.append(e["source_id"])
            ns.append(self.order.index(e["source_id"]) + 1)
        return "".join(f"[{n}]" for n in sorted(set(ns)))

    def lines(self) -> list[str]:
        out = []
        for n, sid in enumerate(self.order, 1):
            d = get_doc(sid) or {}
            when = ", ".join(x for x in (
                f"published {d['published_date'][:10]}" if d.get("published_date") else "",
                f"retrieved {_local_day(d['retrieved_at'])}" if d.get("retrieved_at") else "") if x)
            if d.get("url"):
                out.append(f"{n}. [{d.get('title') or d['url']}]({d['url']}) — {when}")
            else:
                out.append(f"{n}. {d.get('label') or 'Source'} — {d.get('title') or ''} ({when})")
        return out


def _local_day(iso: str) -> str:
    """Retrieval times are stored in UTC; show the calendar day the run happened locally."""
    return datetime.fromisoformat(iso).astimezone().date().isoformat()


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _plain(text: str) -> str:
    """Drop a leading "Assessment:" the model added; the render already labels assessments."""
    return re.sub(r"^\s*assessment\s*[:—-]\s*", "", text or "", flags=re.I)


_ATTR = re.compile(r"^[A-Z][\w.+]*(?:\s+[A-Z][\w.+]*)*\s+(?:reports|lists|estimates|puts)\s+")


def _unattr(value: str) -> str:
    """Snapshot/stack cells render without the "Apollo reports" attribution prefix;
    the attribution stays in the JSON fact."""
    return _ATTR.sub("", value or "", count=1)


_AMOUNT = re.compile(r"\$?(\d[\d,]*(?:\.\d+)?)\s*(k|m|b|bn|thousand|million|billion)?\b", re.I)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}


def _amount(value: str) -> float | None:
    m = _AMOUNT.search(value)
    return float(m[1].replace(",", "")) * _SCALE.get((m[2] or "").lower(), 1) if m else None


def conflicting(facts: list[dict]) -> bool:
    """Sources disagree materially: the first amounts stated differ by more than 25%."""
    amounts = [a for f in facts if (a := _amount(f["value"]))]
    return len(amounts) > 1 and max(amounts) / min(amounts) > CONFLICT_RATIO


def working_fact(facts: list[dict]) -> dict | None:
    """One working number: Apollo-attributed if present, else the first fact."""
    if not facts:
        return None
    return next((f for f in facts if f["value"].startswith("Apollo reports")), facts[0])


_NAMED_AMT = re.compile(
    r"([A-Z][A-Za-z.+]*(?:\sLabs)?)\s+(?:reports|lists|estimates|puts)\s+"
    r"(?:.*?)(~?\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|k|m|b|bn))?|"
    r"~?[\d,]{3,}(?:\.\d+)?(?:\s*(?:million|billion|k|m|b|bn))?)", re.I)


def _agrees(fact: dict, working: dict) -> bool:
    a, b = _amount(fact["value"]), _amount(working["value"])
    return bool(a and b and max(a, b) / min(a, b) <= CONFLICT_RATIO)


def _named_amounts(value: str) -> list[str]:
    return [f"{m[1]} {m[2].strip()}" for m in _NAMED_AMT.finditer(value)]


def working_cell(src: _Sources, facts: list[dict]) -> str:
    """One working value for the row (Apollo preferred); disagreeing sources stay in the JSON."""
    working = working_fact(facts)
    cites = src.cite(working["evidence"])
    extra = [src.cite(f["evidence"]) for f in facts if f is not working and _agrees(f, working)]
    return f"{_unattr(working['value'])} {cites}{''.join(extra)}".rstrip()


def _url(source_id: str) -> str | None:
    return (get_doc(source_id) or {}).get("url")


def _facts(src: _Sources, facts: list[dict], check_conflict: bool = False,
           unattr: bool = False) -> str:
    if not facts:
        return "Not found"
    out = "; ".join(f"{_unattr(f['value']) if unattr else f['value']} {src.cite(f['evidence'])}"
                    for f in facts)
    return out + (" ⚠️ sources differ" if check_conflict and conflicting(facts) else "")


def _dated(e: dict) -> str:
    if not e.get("date"):
        return " · date unknown"
    via = f' (dated by "{_one_line(e["date_quote"])}")' if e.get("date_quote") and not \
        (get_doc(e["source_id"]) or {}).get("published_date") else ""
    stale = " ⚠️ may be outdated" if e.get("stale") else ""
    return f" · {e['date']}{via}{stale}"


def _quotes(src: _Sources, evidence: list[dict], dates: bool = False) -> list[str]:
    return [f'  > "{_one_line(e["quote"])}" {src.cite([e])}{_dated(e) if dates else ""}'
            for e in evidence if e.get("quote")]


def _posted_ago(a: dict) -> str:
    d = a.get("days_ago")
    if d is None:
        dates = [e["date"] for e in a.get("evidence") or [] if e.get("date")]
        if not dates:
            return ""
        d = (date.today() - date.fromisoformat(min(dates))).days
    return "today" if d <= 0 else ("1d ago" if d == 1 else f"{d}d ago")


def card_people(people: list[dict]) -> list[dict]:
    """Who-to-work cards (≤5). Legacy briefs without `featured` show everyone."""
    if any(p.get("featured") is not None for p in people):
        return [p for p in people if p.get("featured")]
    return people


def wider_people(people: list[dict]) -> list[dict]:
    """Verified people for the compact table: team, then name."""
    verified = [p for p in people if p.get("verified")]
    return sorted(verified, key=lambda p: ((p.get("team") or "\uffff").lower(), p["name"].lower()))


def render_markdown(rec: dict) -> str:
    b, src = rec["brief"], _Sources()
    L = [f"# {b['company_name']} — account brief", f"*{rec['date']} · {b['domain']}*", ""]

    if rec.get("since_last_run"):
        s = rec["since_last_run"]
        L += [f"## Since last run ({s['since']})", ""]
        if s["nothing_new"]:
            L.append("Nothing new.")
        else:
            L += [f"- **New signal:** {h}" for h in s["new_signals"]]
            L += [f"- **New contact:** {n}" for n in s["new_contacts"]]
            L += [f"- **Departed:** {n}" for n in s["departed_contacts"]]
            if s["verdict_change"]:
                L.append(f"- **Verdict:** {s['verdict_change']}")
        L.append("")

    v = b.get("verdict")
    L += ["## Verdict", ""]
    if v:
        L += [f"**{VERDICT[v['verdict']]}** *(assessment)* — {_plain(v['why_now'])} {src.cite(v['based_on'])}".rstrip()]
        if v.get("watch_for"):
            L.append(f"\nWatch for: {v['watch_for']}")
    else:
        L.append("No supported verdict.")
    L.append("")

    labels = {"hq": "HQ", "founded": "Founded", "industry": "Industry", "ownership": "Ownership",
              "parent_company": "Parent company", "headcount": "Headcount", "headcount_growth": "Headcount growth",
              "revenue": "Revenue", "funding": "Funding", "sells_to": "Sells to", "products": "Key products"}
    L += ["## Snapshot", "", "| | |", "|---|---|"]
    for k, lab in labels.items():
        if not b["snapshot"][k]:
            continue
        if k in QUANTITATIVE:
            cell = working_cell(src, b["snapshot"][k])
        else:
            cell = _facts(src, b["snapshot"][k], unattr=True)
        L.append(f"| {lab} | {cell} |")
    L.append("")

    labels = {"eng_headcount": "Engineering headcount", "open_eng_roles": "Open eng roles", "tech_stack": "Tech stack",
              "ai_dev_tools": "AI dev tools", "eng_blog_and_github": "Eng blog / GitHub", "ai_posture": "AI posture",
              "priorities": "What they care about", "peers": "Peers"}
    L += ["## Engineering & priorities", ""]
    L += [f"- **{lab}:** {_facts(src, b['engineering'][k], unattr=k in ('eng_headcount', 'tech_stack'))}"
          for k, lab in labels.items()]
    L.append("")

    L += ["## Intent signals", ""]
    if not b["signals"]:
        L.append("No signals met the bar.")
    for s in sort_signals(b["signals"]):
        who = f" · Relevant for: {', '.join(s['who_cares'])}" if s["who_cares"] else ""
        label = SOURCE_LABELS.get(s["source"], s["source"])
        L += [f"**{s['headline']}** — Tier {s['tier']} · {label} · {s['use_case']}{who}"] \
            + _quotes(src, s["evidence"], dates=True) + [""]
    add = b.get("additional_signals") or []
    if add:
        L += ["### Additional signals", "", "*Lower-level signals from the past two weeks.*", ""]
        for a in add:
            ago = _posted_ago(a)
            L += [f"- `{ADD_LABELS.get(a['label'], a['label'])}` **{a['headline']}** — "
                  f"posted {ago} {src.cite(a['evidence'])}".rstrip()]
        L.append("")
    if b["competitive_context"]:
        L += ["**Competitive context**", ""]
        L += [f"{i}. {c['value']} ({c['situation'].replace('_', ' ')}) {src.cite(c['evidence'])}"
              for i, c in enumerate(b["competitive_context"], 1)]
        L.append("")

    L += ["## Company LinkedIn", ""]
    if not b.get("company_linkedin"):
        L.append("Not found")
    for c in b["company_linkedin"]:
        kind = {"original": "company post", "possible_repost": "possible repost"}.get(c["kind"], "post")
        tags = f" · tags: {', '.join(c['tags'])}" if c["tags"] else ""
        L += [f"- {c['value']} *({kind}{tags})* {src.cite(c['evidence'])}"]
        L += _quotes(src, c["evidence"])
    L.append("")

    L += ["## Who to work", ""]
    cards = card_people(b["people"])
    if not cards:
        L += ["Not found", ""]
    for p in cards:
        badges = []
        if p["label"] in ("champion", "warm_lead"):
            r = champion_reason(p)
            name = "Champion" if p["label"] == "champion" else "Warm lead"
            badges.append(f"**{name}{f' ({r})' if r else ''}**")
        if p.get("incumbent_owner"):
            badges.append("**Incumbent owner**")
        tag = (" · ".join(badges) + " · ") if badges else ""
        ver = "verified" if p["verified"] else "single source"
        link = f" · [LinkedIn]({_url(p['profile_source_id'])})" if p.get("profile_source_id") else ""
        role = ROLE.get(p["deal_role"], p["deal_role"])
        L.append(f"- {tag}**{p['name']}** — *{p['title']} · {role}* — {_plain(p['why'])} *({ver}){link}* {src.cite(p['evidence'])}")
        # The line's label follows the person's outcome: champion evidence only on
        # champions, warm-lead evidence only on warm leads, else Supporting.
        qual = {"champion": {"ex_devin_customer"},
                "warm_lead": {"public_pain", "hiring_manager"}}.get(p["label"], set())
        names = {"champion": "Champion evidence", "warm_lead": "Warm lead"}
        for f in p["champion_evidence"]:
            label = names.get(p["label"], "Supporting") if f["kind"] in qual else "Supporting"
            L.append(f"  - {label} ({KIND[f['kind']]}): {f['value']} {src.cite(f['evidence'])}")
        L += [f"  - Hook: {f['value']} {src.cite(f['evidence'])}" for f in p["hooks"]]
        if p.get("reporting_line"):
            L.append(f"  - Reporting line (as stated): {p['reporting_line']['value']} {src.cite(p['reporting_line']['evidence'])}")
    if cards:
        L.append("")

    L += ["## Recommended entry: top 3", ""]
    L += [f"{i}. **{t['name']}** — {_plain(t['why']['text'])} {src.cite(t['why']['based_on'])}" for i, t in enumerate(b["top_contacts"], 1)]
    L += ["" if b["top_contacts"] else "None supported.", ""]

    L += ["## Drafted messages", "", "*Drafts for rep review. Never auto-sent.*", ""]
    for d in b["drafts"]:
        L += [f"### {d['name']}", ""]
        if d["low_confidence"]:
            L += [f"> ⚠️ {LOW_CONFIDENCE}", ""]
        L += ["**Email**", f"*Subject: {d['email']['subject']}*", "", d["email"]["body"], "",
              f"**LinkedIn connection note**", "", d["linkedin_note"], "",
              f"**LinkedIn follow-up**", "", d["linkedin_followup"], ""]
        if d.get("notes"):
            L += ["**Send notes** *(rep only — never sent)*", "", d["notes"], ""]
        L += ["", f"*Built from:* {src.cite(d['built_from'])}"]
        L += [f"*Lint ({x['channel']}):* {x['issue']}" for x in d["lint"]] + [""]

    L += ["## ABM themes *(assessment)*", ""]
    L += [f"- {_plain(a['text'])} {src.cite(a['based_on'])}" for a in b["abm_themes"]] or ["None."]
    L += ["", "## Wider buying center", ""]
    wider = wider_people(b["people"])
    if not wider:
        L.append("Not found")
    else:
        L += ["| Name | Title | Team | Deal role | LinkedIn |", "|---|---|---|---|---|"]
        for p in wider:
            li = f"[LinkedIn]({_url(p['profile_source_id'])})" if p.get("profile_source_id") and _url(p["profile_source_id"]) else "—"
            L.append(f"| {p['name']} | {p['title']} | {p.get('team') or '—'} | {ROLE.get(p['deal_role'], p['deal_role'])} | {li} |")
    L += ["", "## Org chart *(Sumble — inferred)*", ""]
    oc = (get_doc(b["org_chart"]) or {}).get("data") if b.get("org_chart") else None
    if not oc or not oc.get("decision_makers"):
        L.append("Not found")
    else:
        L.append(f"*{oc.get('name') or b['domain']} — reporting lines inferred by Sumble, "
                 f"not confirmed. (0.x) is Sumble's inference confidence.*")
        L.append("")
        for dm in oc["decision_makers"]:
            role = dm["role"].replace("_", " ")
            mgr = ((dm.get("manager") or {}).get("attributes") or {})
            line = f"- **{dm['name']}**, {dm['title']} *({role})*"
            if not dm.get("matched"):
                line += " *(Sumble only — not yet sourced)*"
            if mgr.get("name"):
                line += f" — reports to {mgr['name']}"
            if dm.get("linkedin"):
                line += f" · [LinkedIn]({dm['linkedin']})"
            L.append(line)
            for r in dm.get("reports") or []:
                conf = f" ({r['confidence']:.1f})" if r.get("confidence") else ""
                li = f" · [LinkedIn]({r['linkedin']})" if r.get("linkedin") else ""
                L.append(f"  - {r['name']} — {r['title']}{conf}{li}")
        if oc.get("unmatched_people"):
            L.append(f"\n*No Sumble match for: {', '.join(oc['unmatched_people'])} "
                     "(no inferred reports available).*")
    L += ["", "## Sources", ""] + src.lines()

    rep = rec["report"]
    L += ["", f"*Validator: {rep['dropped_count']} unsupported item(s) dropped, {len(rep['flags'])} flag(s).*"]
    return "\n".join(L) + "\n"


def render_slack(rec: dict) -> str:
    b = rec["brief"]
    v = b.get("verdict")
    L = [f"{b['company_name']} — {VERDICT[v['verdict']] if v else 'No supported verdict'}"]
    if v:
        L.append(f"Why now: {_plain(v['why_now'])}")
    top = sort_signals(b["signals"])[:3]
    if top:
        L.append("Signals:")
        L += [f" • {s['headline']} ({_url(s['evidence'][0]['source_id']) or 'source in doc'})" for s in top]
    if b["top_contacts"]:
        titles = {p["name"]: p["title"] for p in b["people"]}
        L.append("Start with:")
        L += [f" {i}. {t['name']}, {titles.get(t['name'], '')} — {_plain(t['why']['text'])}"
              for i, t in enumerate(b["top_contacts"], 1)]
    L.append(f"Report (leave feedback): {report_link(rec)}")
    L.append(f"Full brief (.docx): {brief_link(rec)}")
    return "\n".join(L)
