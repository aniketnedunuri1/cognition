"""Parse a date out of a verified quote (a byline or dateline), deterministically.

Exactly one distinct date must appear, or the result is None: no guessing between
candidates. Month-only dates ("April 2026") resolve to the 1st, the strict choice
for recency checks. Numeric dates other than ISO (07.02.26, 7/2/26) are ambiguous
across locales and are not parsed.
"""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {m: i for i, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
     ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
     ("oct", "october"), ("nov", "november"), ("dec", "december")], 1) for m in names}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"

_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I),
     lambda m: (int(m[3]), _MONTHS[m[1].lower()], int(m[2]))),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON},?\s+(\d{{4}})\b", re.I),
     lambda m: (int(m[3]), _MONTHS[m[2].lower()], int(m[1]))),
    (re.compile(rf"\b{_MON},?\s+(\d{{4}})\b", re.I), lambda m: (int(m[2]), _MONTHS[m[1].lower()], 1)),
]


def parse_date(text: str, today: date | None = None) -> date | None:
    """The single date stated in `text`, or None if there's none, several, or it's in the future."""
    found, spans = set(), []
    for pattern, to_ymd in _PATTERNS:
        for m in pattern.finditer(text or ""):
            if any(s <= m.start() < e for s, e in spans):  # already matched by a more specific pattern
                continue
            try:
                found.add(date(*to_ymd(m)))
                spans.append(m.span())
            except ValueError:
                continue
    if len(found) != 1:
        return None
    d = found.pop()
    return None if d > (today or date.today()) else d
