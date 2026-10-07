"""Devin positioning knowledge: incremental refresh + read access.

refresh_positioning() finds what's new (sitemap diff + third-party mentions) and
returns it; it decides nothing. The model classifies items and records them with
apply_knowledge_update(), which enforces the source rules in code:
  - every proof quote must verify against its cached source
  - public_reference is computed from the source domain (Cognition-owned only)
  - positioning.md may cite Cognition-owned sources only
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import exa
from .cache import ROOT, get_doc, mode, now_iso, site
from .exa import ProviderError, _norm_company
from .notify import notify
from .vcs import commit
from .verify import normalize, verify_quote

EXCERPT_CHARS = 2500
FIRST_RUN_LOOKBACK_DAYS = 365


def kdir() -> Path:
    return Path(os.environ.get("KNOWLEDGE_DIR", ROOT / "knowledge"))


def _load(name: str, default):
    try:
        return json.loads((kdir() / name).read_text())
    except FileNotFoundError:
        return default


def _save(name: str, data) -> None:
    (kdir() / name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def _config() -> dict:
    return _load("sources.json", {})


def is_cognition_owned(url: str | None) -> bool:
    host = site(url) or ""
    return any(host == d or host.endswith("." + d) for d in _config().get("cognition_domains", []))


# ---------- refresh ----------

def _sitemap(url: str) -> list[tuple[str, str | None]]:
    r = httpx.get(url, follow_redirects=True, timeout=30)
    r.raise_for_status()
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>\s*(?:<lastmod>\s*([^<\s]+)\s*</lastmod>)?", r.text)


def _wanted(url: str, rules: dict) -> bool:
    path = urlsplit(url).path or "/"
    return (any(re.search(p, path) for p in rules.get("include", []))
            and not any(re.search(p, path) for p in rules.get("exclude", [])))


def _sha(text: str | None) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def refresh_positioning(limit: int = 15) -> dict:
    """Return up to `limit` new or changed items to classify. Call apply_knowledge_update
    with the processed source_ids, then call again until items is empty."""
    if mode() == "replay":
        raise ProviderError("refresh_positioning needs live sources (replay mode)")
    cfg, seen = _config(), _load("seen.json", {"last_refresh": None, "urls": {}, "pending": {}})

    candidates, unchanged = [], 0
    for sm in cfg.get("sitemaps", []):
        for url, lastmod in _sitemap(sm["url"]):
            if not _wanted(url, sm):
                continue
            prev = seen["urls"].get(url)
            if prev and prev.get("lastmod") == lastmod:
                unchanged += 1
            else:
                candidates.append((url, lastmod, prev))

    items, errors, looked = [], [], 0
    for url, lastmod, prev in candidates:
        if len(items) >= limit:
            break
        looked += 1
        page = exa.fetch_page(url, force=bool(prev))
        if page["status"] != "ok":
            errors.append({"url": url, "reason": page["reason"]})
            continue
        doc = get_doc(page["source_id"])
        sha = _sha(doc["text"])
        if prev and prev.get("sha") == sha:  # site rebuild bumped lastmod; content same
            seen["urls"][url] = {**prev, "lastmod": lastmod}
            continue
        seen["pending"][doc["source_id"]] = {"url": url, "lastmod": lastmod, "kind": "cognition"}
        items.append({"source": "cognition", "change": "changed" if prev else "new",
                      "source_id": doc["source_id"], "title": doc["title"], "site": site(url),
                      "path": urlsplit(url).path, "published_date": doc["published_date"],
                      "excerpt": (doc["text"] or "")[:EXCERPT_CHARS],
                      "total_chars": len(doc["text"] or "")})

    remaining = len(candidates) - looked
    if len(items) < limit and remaining == 0:
        since = (seen["last_refresh"] or
                 (date.today() - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)).isoformat())[:10]
        done = set(seen["urls"]) | set(seen.get("third_party", {})) | {p["url"] for p in seen["pending"].values()}
        for q in cfg.get("third_party_queries", []):
            for r in exa.search_web(q, since=since):
                doc = get_doc(r["source_id"])
                if len(items) >= limit or doc["url"] in done or is_cognition_owned(doc["url"]):
                    continue
                done.add(doc["url"])
                seen["pending"][doc["source_id"]] = {"url": doc["url"], "kind": "third_party"}
                items.append({"source": "third_party", "change": "new", "source_id": doc["source_id"],
                              "title": doc["title"], "site": site(doc["url"]),
                              "published_date": doc["published_date"], "highlights": doc["highlights"]})

    if not items and remaining == 0:
        seen["last_refresh"] = now_iso()
    _save("seen.json", seen)
    return {"items": items, "remaining_pages": remaining, "unchanged_pages": unchanged,
            "fetch_errors": errors}


# ---------- apply ----------

def _named_in_body(doc: dict, name: str) -> bool:
    """Name appears in the page body, not just its title. Catches a cached page whose
    body is a different company's story under this company's title."""
    body = (doc.get("text") or "").replace(doc.get("title") or "\0", " ")
    return normalize(name) in normalize(body)


def _proof(source_id: str, quote: str) -> tuple[dict | None, str | None]:
    check = verify_quote(source_id, quote)
    if not check["verified"]:
        return None, check["reason"]
    doc = get_doc(source_id)
    return {"quote": quote, "source_id": source_id, "url": doc["url"],
            "published_date": doc["published_date"], "cognition_owned": is_cognition_owned(doc["url"])}, None


def apply_knowledge_update(
    processed_source_ids: list[str],
    summary: str,
    customers: list[dict] | None = None,
    use_cases: list[dict] | None = None,
    positioning_markdown: str | None = None,
) -> dict:
    """Record classified items.

    customers: [{name, use_case, quote, source_id, metric?}]
    use_cases: [{name, description, quote, source_id, customer?}]
    positioning_markdown: full rewrite, only for a real positioning shift; cite
    claims as [src_...] — Cognition-owned sources only.
    """
    today = date.today().isoformat()
    rejected = []

    cdata = _load("customers.json", {"last_updated": None, "customers": []})
    by_name = {_norm_company(c["name"]): c for c in cdata["customers"]}
    added_customers = 0
    for c in customers or []:
        proof, err = _proof(c.get("source_id", ""), c.get("quote", ""))
        if not err and not _named_in_body(get_doc(c["source_id"]), c["name"]):
            err = (f"{c['name']!r} not named in the page body — cached content may be the wrong page; "
                   "re-fetch with fetch_page(force=True)")
        if err:
            rejected.append({"customer": c.get("name"), "reason": err})
            continue
        proof.update(use_case=c.get("use_case"), metric=c.get("metric"))
        key = _norm_company(c["name"])
        if key not in by_name:
            by_name[key] = {"name": c["name"], "public_reference": False, "proofs": []}
            cdata["customers"].append(by_name[key])
            added_customers += 1
        rec = by_name[key]
        if not any(p["source_id"] == proof["source_id"] and p["quote"] == proof["quote"] for p in rec["proofs"]):
            rec["proofs"].append(proof)
        # Only Cognition itself can make a customer nameable in outreach.
        rec["public_reference"] = any(p["cognition_owned"] for p in rec["proofs"])

    udata = _load("use_cases.json", {"last_updated": None, "use_cases": []})
    by_uc = {u["name"].lower(): u for u in udata["use_cases"]}
    added_use_cases = 0
    for u in use_cases or []:
        proof, err = _proof(u.get("source_id", ""), u.get("quote", ""))
        if err:
            rejected.append({"use_case": u.get("name"), "reason": err})
            continue
        if u["name"].lower() not in by_uc:
            if not proof["cognition_owned"]:
                rejected.append({"use_case": u["name"], "reason": "new use cases come from Cognition-owned sources only"})
                continue
            by_uc[u["name"].lower()] = {"name": u["name"], "description": u.get("description", ""), "proofs": []}
            udata["use_cases"].append(by_uc[u["name"].lower()])
            added_use_cases += 1
        proof["customer"] = u.get("customer")
        rec = by_uc[u["name"].lower()]
        if not any(p["source_id"] == proof["source_id"] and p["quote"] == proof["quote"] for p in rec["proofs"]):
            rec["proofs"].append(proof)

    rewritten = False
    if positioning_markdown:
        cited = set(re.findall(r"\[(src_[0-9a-f]{10})\]", positioning_markdown))
        bad = sorted(s for s in cited if not (get_doc(s) and is_cognition_owned(get_doc(s)["url"])))
        if not cited or bad:
            rejected.append({"positioning": "rewrite rejected",
                             "reason": "must cite Cognition-owned sources only" + (f"; bad: {bad}" if bad else "; none cited")})
        else:
            (kdir() / "positioning.md").write_text(f"<!-- last_updated: {today} -->\n{positioning_markdown.strip()}\n")
            rewritten = True

    seen = _load("seen.json", {"last_refresh": None, "urls": {}, "pending": {}})
    for sid in processed_source_ids:
        p = seen["pending"].pop(sid, None)
        if p and p["kind"] == "cognition":
            seen["urls"][p["url"]] = {"lastmod": p["lastmod"], "sha": _sha((get_doc(sid) or {}).get("text")),
                                      "source_id": sid, "processed": today}
        elif p:
            seen.setdefault("third_party", {})[p["url"]] = {"source_id": sid, "processed": today}

    if added_customers or customers:
        cdata["last_updated"] = today
        cdata["customers"].sort(key=lambda c: c["name"].lower())
    if added_use_cases or use_cases:
        udata["last_updated"] = today
    _save("customers.json", cdata)
    _save("use_cases.json", udata)
    _save("seen.json", seen)

    notified = notify(f"Devin positioning changed: {summary}") if rewritten else None
    committed = commit(kdir(), f"knowledge: {summary}")
    return {"customers_added": added_customers, "use_cases_added": added_use_cases,
            "positioning_rewritten": rewritten, "rejected": rejected,
            "slack": notified, "committed": committed}


# ---------- read ----------

def get_positioning() -> dict:
    """Positioning narrative, use cases with proof, and customers nameable in outreach."""
    pos = kdir() / "positioning.md"
    customers = _load("customers.json", {"customers": []})["customers"]
    return {
        "positioning": pos.read_text() if pos.exists() else "Not found: positioning not built yet",
        "use_cases": _load("use_cases.json", {"use_cases": []})["use_cases"],
        "public_customers": [
            {"name": c["name"], "proofs": [p for p in c["proofs"] if p["cognition_owned"]]}
            for c in customers if c["public_reference"]
        ],
        "non_public_customer_count": sum(not c["public_reference"] for c in customers),
    }


def use_case_names() -> set[str]:
    return {u["name"] for u in _load("use_cases.json", {"use_cases": []})["use_cases"]}


def match_customers(companies: list[str]) -> list[dict]:
    """Which of these companies are known Devin customers (peer proof, champion tracking).
    public_reference false = may inform targeting, never be named in a message."""
    customers = {_norm_company(c["name"]): c for c in _load("customers.json", {"customers": []})["customers"]}
    out = []
    for name in companies:
        c = customers.get(_norm_company(name))
        if c:
            out.append({"company": name, "customer": c["name"], "public_reference": c["public_reference"],
                        "use_cases": sorted({p["use_case"] for p in c["proofs"] if p.get("use_case")}),
                        "proof_source_ids": [p["source_id"] for p in c["proofs"]]})
    return out
