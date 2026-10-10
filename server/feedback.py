"""Feedback memory: learned rules + golden examples, in playbook/ and git-tracked.

Layers, merged at read time (a rep's own rules apply only to their drafts):
  locked_rules.json   — policy set by the lead in code review; nobody can override it
  learned_rules.json  — team defaults; changed only by a promotion PR
  reps/<rep>.json     — one file per rep, their own rules + golden examples

When PROMOTE_THRESHOLD distinct reps hold the same rule, it is promoted to the team
defaults by opening a PR (the lead's "second yes"). Rollback = `git revert`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

from .cache import ROOT
from .vcs import commit, open_pr

DEAL_ROLES = ("economic_buyer", "technical_evaluator", "gatekeeper", "parent_org_approver")
CHANNELS = ("email", "linkedin_note", "linkedin_followup")
EXAMPLES_PER_SLOT = 3
PROMOTE_THRESHOLD = 3


def pdir() -> Path:
    return Path(os.environ.get("PLAYBOOK_DIR", ROOT / "playbook"))


def rep_slug(rep: str | None) -> str | None:
    """Slack handle / email local-part → a safe filename and display name."""
    slug = re.sub(r"[^a-z0-9_-]", "", (rep or "").lower().split("@")[0])
    return slug or None


def _load(name: str, default):
    try:
        return json.loads((pdir() / name).read_text())
    except FileNotFoundError:
        return default


def _save(name: str, data) -> None:
    (pdir() / name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def _rep_path(slug: str) -> Path:
    return pdir() / "reps" / f"{slug}.json"


def _load_rep(slug: str) -> dict:
    try:
        data = json.loads(_rep_path(slug).read_text())
        return {"rules": data.get("rules") or [], "golden_examples": data.get("golden_examples") or {}}
    except FileNotFoundError:
        return {"rules": [], "golden_examples": {}}


def _save_rep(slug: str, data: dict) -> None:
    _rep_path(slug).parent.mkdir(parents=True, exist_ok=True)
    _rep_path(slug).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def _key(rule: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", rule.lower()).strip()


def _render_rules(rules: list[dict]) -> None:
    lines = ["# Learned rules", "",
             "Team defaults, promoted from rep-level rules by reviewed PRs. Rep-level rules live in "
             "`reps/`. Edit via the tool; rollback = `git revert`.", "",
             "| Rule | Scope | Evidence | Sponsors | Added |", "|---|---|---|---|---|"]
    for r in rules:
        sponsors = ", ".join(r.get("sponsors") or []) or "—"
        lines.append(f"| {r['rule']} | {r['scope']} | {r['evidence']} | {sponsors} | {r['added']} |")
    (pdir() / "learned_rules.md").write_text("\n".join(lines) + "\n")


def get_playbook(rep: str | None = None) -> dict:
    """Everything that applies to this rep's run: locked rules (never overridable),
    team defaults, the rep's own rules, and golden examples (the rep's first)."""
    out = {"locked_rules": _load("locked_rules.json", []),
           "learned_rules": _load("learned_rules.json", []),
           "golden_examples": _load("golden_examples.json", {}),
           "rep": rep_slug(rep)}
    if out["rep"]:
        own = _load_rep(out["rep"])
        out["rep_rules"] = [r for r in own["rules"] if not r.get("promoted")]
        for slot, items in own["golden_examples"].items():
            merged = items + out["golden_examples"].get(slot, [])
            out["golden_examples"][slot] = merged[:EXAMPLES_PER_SLOT]
    return out


def _converging_reps(key: str) -> list[str]:
    """Reps whose file holds this rule (by normalized key) and who haven't had it promoted."""
    reps_dir = pdir() / "reps"
    if not reps_dir.is_dir():
        return []
    hits = []
    for f in sorted(reps_dir.glob("*.json")):
        rules = _load_rep(f.stem)["rules"]
        if any(_key(r["rule"]) == key and not r.get("promoted") for r in rules):
            hits.append(f.stem)
    return hits


def add_playbook_rule(rule: str, scope: str, rep: str | None = None,
                      replaces: str | None = None) -> dict:
    """Add a rule to the giving rep's own layer (their drafts only). `rep` is required
    — rules are never anonymous. `replaces` must be one of that rep's own rules;
    team defaults change only via promotion PRs, locked rules never."""
    if scope != "global" and not (scope.startswith("persona:") and scope.split(":", 1)[1] in DEAL_ROLES):
        return {"ok": False, "reason": f"scope must be 'global' or 'persona:<{'|'.join(DEAL_ROLES)}>'"}
    rule = rule.strip()
    if replaces and any(_key(r["rule"]) == _key(replaces) for r in _load("locked_rules.json", [])):
        return {"ok": False, "reason": "locked rules are set by the lead and can't be replaced"}

    slug = rep_slug(rep)
    if not slug:
        return {"ok": False,
                "reason": "rep is required — resolve the sender's handle (whoami / Slack sender) first. "
                          "Never record anonymous or self-declared feedback."}
    own = _load_rep(slug)
    rules = own["rules"]
    layer = f"{slug}'s own rules"
    removed = None

    if replaces:
        match = [r for r in rules if _key(r["rule"]) == _key(replaces)]
        if not match:
            return {"ok": False, "reason": f"rule to replace not found in {layer}", "existing": rules}
        removed = match[0]
        rules.remove(removed)

    existing = next((r for r in rules if _key(r["rule"]) == _key(rule) and r["scope"] == scope), None)
    if existing:
        existing["evidence"] += 1
    else:
        rules.append({"rule": rule, "scope": scope, "evidence": 1, "added": date.today().isoformat()})

    _save_rep(slug, own)
    committed = commit(pdir(), f"playbook: learned rule ({scope}, {slug}): {rule[:60]}")

    promotion = _maybe_promote(rule, scope)
    out = {"ok": True,
           "confirmation": f"Learned: {rule}. Applies to: {scope} — {slug}'s drafts only.",
           "replaced": removed["rule"] if removed else None,
           "evidence": existing["evidence"] if existing else 1, "committed": committed}
    if promotion:
        out["promotion"] = promotion
        out["confirmation"] += (f" {len(promotion['reps'])} reps converged on this rule — "
                                f"promoted to team defaults for review: {promotion['pr_url'] or 'PR failed'}.")
    return out


def _maybe_promote(rule: str, scope: str) -> dict | None:
    """When PROMOTE_THRESHOLD distinct reps independently hold the same rule, move it
    to team defaults via a reviewed PR instead of any single rep's say-so."""
    key = _key(rule)
    if any(_key(r["rule"]) == key for r in _load("learned_rules.json", [])):
        return None
    reps = _converging_reps(key)
    if len(reps) < PROMOTE_THRESHOLD:
        return None

    rules = _load("learned_rules.json", [])
    rules.append({"rule": rule, "scope": scope, "evidence": len(reps),
                  "sponsors": reps, "added": date.today().isoformat()})
    _save("learned_rules.json", rules)
    _render_rules(rules)
    for slug in reps:
        own = _load_rep(slug)
        for r in own["rules"]:
            if _key(r["rule"]) == key:
                r["promoted"] = True
        _save_rep(slug, own)

    branch = f"playbook-promote-{hashlib.sha1(key.encode()).hexdigest()[:10]}"
    pr_url = open_pr(pdir(), branch,
                     f"playbook: promote rep rule to team default ({scope})",
                     f"Promote rule to team default: {rule[:70]}",
                     "## Summary\n\n"
                     f"{len(reps)} reps independently gave this feedback, so it converged to a "
                     f"team-level default:\n\n> {rule}\n\n"
                     f"- Scope: `{scope}`\n- Reps: {', '.join(reps)}\n\n"
                     "Auto-promoted by `add_playbook_rule`. Review like any other playbook change; "
                     "merging updates every rep's Devin.\n\nGenerated with Devin")
    return {"reps": reps, "pr_url": pr_url, "branch": branch}


def add_golden_example(persona: str, channel: str, message: str, subject: str | None = None,
                       rep: str | None = None) -> dict:
    """Store an approved message as a style reference in the approving rep's own
    examples (newest few per persona + channel). `rep` is required."""
    if persona not in DEAL_ROLES or channel not in CHANNELS:
        return {"ok": False, "reason": f"persona in {DEAL_ROLES}, channel in {CHANNELS}"}
    slug = rep_slug(rep)
    if not slug:
        return {"ok": False, "reason": "rep is required — resolve the sender's handle first."}
    slot = f"{persona}/{channel}"
    entry = {"subject": subject, "message": message, "approved": date.today().isoformat()}
    own = _load_rep(slug)
    ex = own["golden_examples"].setdefault(slot, [])
    ex.insert(0, entry)
    del ex[EXAMPLES_PER_SLOT:]
    _save_rep(slug, own)
    committed = commit(pdir(), f"playbook: golden example ({slot}, {slug})")
    return {"ok": True, "kept": len(ex), "rep": slug, "committed": committed}
