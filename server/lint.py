"""lint_message: deterministic checks on a draft before a rep sees it.

severity "block": a rule that must never be broken (unsourced number, layoffs, a
non-public customer, Windsurf, placeholders, banned phrases). The validator drops
drafts with blocking issues. severity "fix": format (length, ask count).
"""

from __future__ import annotations

import re

from .cache import get_doc
from .knowledge import _load
from .verify import normalize

LIMITS = {
    "email": {"subject_words": (4, 6), "body_words": (75, 120)},
    "linkedin_note": {"chars": (1, 300)},
    "linkedin_followup": {"chars": (400, 600)},
}
BANNED = ["hope this finds you well", "hope you're well", "hope you are well", "quick question",
          "revolutionize", "revolutionary", "game-changer", "game changer", "synergy", "circle back",
          "touch base", "just following up", "just checking in", "cutting-edge", "best-in-class",
          "world-class", "replace your engineers", "replace engineers", "replacing engineers"]
LAYOFFS = re.compile(r"\blay ?offs?\b|\blaid off\b|\bjob cuts\b|\breduction in force\b|\bdownsiz|"
                     r"\bheadcount reduction|\bRIF\b", re.I)
PLACEHOLDER = re.compile(r"\[[^\]]{1,40}\]|\{\{?[^}]{0,40}\}?\}|<[a-z_ ]{2,30}>", re.I)
NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?(?:%|\+|(?:x|k|m|bn|b)\b)?", re.I)  # "8x", "40%", "$10M"; not "17 m(igration)"


def _num_core(tok: str) -> str:
    return tok.lower().replace("$", "").replace(",", "").replace(" ", "")


def _source_text(source_ids: list[str]) -> str:
    parts = []
    for sid in source_ids:
        doc = get_doc(sid) or {}
        parts += [doc.get("title") or "", doc.get("text") or "", *doc.get("highlights", []),
                  str(doc.get("data") or "")]
    return normalize(" ".join(parts)).replace(",", "").replace(" ", "")


def lint_message(channel: str, text: str, source_ids: list[str] | None = None,
                 subject: str | None = None) -> dict:
    issues: list[dict] = []

    def issue(severity: str, msg: str):
        issues.append({"severity": severity, "issue": msg})

    if channel not in LIMITS:
        return {"ok": False, "issues": [{"severity": "block", "issue": f"unknown channel {channel!r}"}]}
    full = f"{subject or ''}\n{text}"
    low = normalize(full)

    # format
    words, chars = len(text.split()), len(text)
    lim = LIMITS[channel]
    if channel == "email":
        n = len((subject or "").split())
        lo, hi = lim["subject_words"]
        if not lo <= n <= hi:
            issue("fix", f"subject has {n} words; needs {lo}-{hi}")
        lo, hi = lim["body_words"]
        if not lo <= words <= hi:
            issue("fix", f"body has {words} words; needs {lo}-{hi}")
        if text.count("?") > 1:
            issue("fix", "more than one question: keep one ask")
    else:
        lo, hi = lim["chars"]
        if not lo <= chars <= hi:
            issue("fix", f"{chars} characters; needs {lo}-{hi}")

    # never
    for phrase in BANNED:
        if phrase in low:
            issue("block", f"banned phrase: {phrase!r}")
    if "windsurf" in low:
        issue("block", "never say Windsurf: the product is Devin (Devin Desktop)")
    if LAYOFFS.search(full):
        issue("block", "never mention layoffs")
    if m := PLACEHOLDER.search(full):
        issue("block", f"placeholder left in: {m.group(0)!r}")
    for c in _load("customers.json", {"customers": []})["customers"]:
        if not c["public_reference"] and re.search(rf"\b{re.escape(normalize(c['name']))}\b", low):
            issue("block", f"{c['name']} is not a public reference: never name them")

    # every number must come from a cited source
    nums = {t.strip() for t in NUMBER.findall(full) if re.search(r"\d", t)}
    if nums:
        hay = _source_text(source_ids or [])
        missing = sorted(t for t in nums if _num_core(t) not in hay)
        if missing:
            issue("block", f"numbers not found in the cited sources: {missing}")

    return {"ok": not issues, "issues": issues, "words": words, "chars": chars}
