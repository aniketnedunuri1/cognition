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
    "email": {"body_words": (75, 120)},
    "linkedin_note": {"chars": (1, 300)},
    "linkedin_followup": {"chars": (400, 600)},
}
BANNED = ["hope this finds you well", "hope you're well", "hope you are well", "quick question",
          "revolutionize", "revolutionary", "game-changer", "game changer", "synergy", "circle back",
          "touch base", "just following up", "just checking in", "cutting-edge", "best-in-class",
          "world-class", "replace your engineers", "replace engineers", "replacing engineers",
          "compare notes", "comparing notes"]
LAYOFFS = re.compile(r"\blay ?offs?\b|\blaid off\b|\bjob cuts\b|\breduction in force\b|\bdownsiz|"
                     r"\bheadcount reduction|\bRIF\b", re.I)
PLACEHOLDER = re.compile(r"\[[^\]]{1,40}\]|\{\{?[^}]{0,40}\}?\}|<[a-z_ ]{2,30}>", re.I)
NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?(?:%|\+|(?:x|k|m|bn|b)\b)?", re.I)  # "8x", "40%", "$10M"; not "17 m(igration)"


# Spelled-out quantities and their digit forms. "one" is excluded: it's ubiquitous in prose
# ("one more angle") and in nearly every source, so checking it would prove nothing.
_UNITS = ["two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
          "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
NUMBER_WORDS = {w: str(i) for i, w in enumerate(_UNITS, 2)}
NUMBER_WORDS |= {w: str(n) for w, n in [("twenty", 20), ("thirty", 30), ("forty", 40), ("fifty", 50),
                                        ("sixty", 60), ("seventy", 70), ("eighty", 80), ("ninety", 90)]}
NUMBER_WORDS |= {"dozen": "12", "dozens": "12", "hundred": "100", "hundreds": "100", "thousand": "1000",
                 "thousands": "1000", "million": None, "millions": None, "billion": None, "billions": None,
                 "twice": "2x", "double": "2x", "doubled": "2x", "triple": "3x", "tripled": "3x",
                 "quadruple": "4x", "half": "50%", "halved": "50%"}
# The meeting ask ("worth 20 minutes?", "a fifteen-minute call") is not a claim about anyone.
# Narrow on purpose: "cut 40 minutes off every build" is a claim and stays checked.
_N = r"(?:\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"
MEETING_LENGTH = re.compile(
    rf"\b{_N}[\s-]+min(?:ute)?s?\s+(?:call|chat|meeting|conversation|walkthrough|demo|sync)\b"
    rf"|\b(?:worth|have|spare|grab|find|would|could|is)\s+(?:a\s+)?{_N}[\s-]+min(?:ute)?s?\b", re.I)
WORD_NUMBER = re.compile(r"\b(" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")\b", re.I)


def _num_core(tok: str) -> str:
    return tok.lower().replace("$", "").replace(",", "").replace(" ", "")


def _source_text(source_ids: list[str]) -> str:
    """Normalized text of the cited sources (with spaces; commas dropped from numbers)."""
    parts = []
    for sid in source_ids:
        doc = get_doc(sid) or {}
        parts += [doc.get("title") or "", doc.get("text") or "", *doc.get("highlights", []),
                  str(doc.get("data") or "")]
    return re.sub(r"(?<=\d),(?=\d)", "", normalize(" ".join(parts)))


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
        if not re.match(r"^Cognition\s*<>\s*\S", (subject or "").strip()):
            issue("fix", 'subject should be "Cognition <> <Company>"')
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
    if any(d in text for d in ("—", "–", "―")):
        issue("block", "no em/en dashes: use commas, periods or parentheses")
    if LAYOFFS.search(full):
        issue("block", "never mention layoffs")
    if m := PLACEHOLDER.search(full):
        issue("block", f"placeholder left in: {m.group(0)!r}")
    for c in _load("customers.json", {"customers": []})["customers"]:
        if not c["public_reference"] and re.search(rf"\b{re.escape(normalize(c['name']))}\b", low):
            issue("block", f"{c['name']} is not a public reference: never name them")

    # every number must come from a cited source
    claims_text = MEETING_LENGTH.sub(" ", full)
    nums = {t.strip() for t in NUMBER.findall(claims_text) if re.search(r"\d", t)}
    words = {m.lower() for m in WORD_NUMBER.findall(claims_text)}
    if nums or words:
        hay = _source_text(source_ids or [])
        compact = hay.replace(" ", "")
        missing = sorted(t for t in nums if _num_core(t) not in compact)
        def sourced(w: str) -> bool:
            digits = NUMBER_WORDS[w]
            return bool(re.search(rf"\b{w}\b", hay) or
                        (digits and re.search(rf"(?<![\d.]){re.escape(digits)}(?!\d)", hay)))
        missing += sorted(w for w in words if not sourced(w))
        if missing:
            issue("block", f"numbers not found in the cited sources: {missing}")

    return {"ok": not issues, "issues": issues, "words": words, "chars": chars}
