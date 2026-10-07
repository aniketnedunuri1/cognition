"""Feedback memory: learned rules + golden examples, in playbook/ and git-tracked."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

from .cache import ROOT
from .vcs import commit

DEAL_ROLES = ("economic_buyer", "champion", "technical_evaluator", "gatekeeper", "parent_org_approver")
CHANNELS = ("email", "linkedin_note", "linkedin_followup")
EXAMPLES_PER_SLOT = 3


def pdir() -> Path:
    return Path(os.environ.get("PLAYBOOK_DIR", ROOT / "playbook"))


def _load(name: str, default):
    try:
        return json.loads((pdir() / name).read_text())
    except FileNotFoundError:
        return default


def _save(name: str, data) -> None:
    (pdir() / name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def _key(rule: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", rule.lower()).strip()


def _render_rules(rules: list[dict]) -> None:
    lines = ["# Learned rules", "",
             "Distilled from rep feedback by `add_playbook_rule`. Edit via the tool; rollback = `git revert`.", "",
             "| Rule | Scope | Evidence | Added |", "|---|---|---|---|"]
    lines += [f"| {r['rule']} | {r['scope']} | {r['evidence']} | {r['added']} |" for r in rules]
    (pdir() / "learned_rules.md").write_text("\n".join(lines) + "\n")


def get_playbook() -> dict:
    """Learned rules (apply them) and golden examples (style references for drafting)."""
    return {"learned_rules": _load("learned_rules.json", []),
            "golden_examples": _load("golden_examples.json", {})}


def add_playbook_rule(rule: str, scope: str, replaces: str | None = None) -> dict:
    """Add a rule, or count another piece of evidence for an existing one.
    replaces: text of an existing rule the rep said this one supersedes."""
    if scope != "global" and not (scope.startswith("persona:") and scope.split(":", 1)[1] in DEAL_ROLES):
        return {"ok": False, "reason": f"scope must be 'global' or 'persona:<{'|'.join(DEAL_ROLES)}>'"}
    rules = _load("learned_rules.json", [])
    removed = None
    if replaces:
        match = [r for r in rules if _key(r["rule"]) == _key(replaces)]
        if not match:
            return {"ok": False, "reason": "rule to replace not found", "existing": rules}
        removed = match[0]
        rules.remove(removed)

    existing = next((r for r in rules if _key(r["rule"]) == _key(rule) and r["scope"] == scope), None)
    if existing:
        existing["evidence"] += 1
    else:
        rules.append({"rule": rule.strip(), "scope": scope, "evidence": 1, "added": date.today().isoformat()})
    _save("learned_rules.json", rules)
    _render_rules(rules)
    committed = commit(pdir(), f"playbook: learned rule ({scope}): {rule.strip()[:60]}")
    return {"ok": True, "confirmation": f"Learned: {rule.strip()}. Applies to: {scope}.",
            "replaced": removed["rule"] if removed else None,
            "evidence": existing["evidence"] if existing else 1, "committed": committed}


def add_golden_example(persona: str, channel: str, message: str, subject: str | None = None) -> dict:
    """Store an approved message as a style reference (newest few per persona + channel)."""
    if persona not in DEAL_ROLES or channel not in CHANNELS:
        return {"ok": False, "reason": f"persona in {DEAL_ROLES}, channel in {CHANNELS}"}
    ex = _load("golden_examples.json", {})
    slot = ex.setdefault(f"{persona}/{channel}", [])
    slot.insert(0, {"subject": subject, "message": message, "approved": date.today().isoformat()})
    del slot[EXAMPLES_PER_SLOT:]
    _save("golden_examples.json", ex)
    committed = commit(pdir(), f"playbook: golden example ({persona}/{channel})")
    return {"ok": True, "kept": len(slot), "committed": committed}
