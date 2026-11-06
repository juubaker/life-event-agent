"""Deterministic date resolution.

The LLM extracts the date *phrase* exactly as the employee wrote it;
this module turns it into a calendar date relative to `today`. Models
are good at spotting "last Saturday" and bad at counting back to it.

Returns None for anything vague ("next month") or unrecognized, so the
caller falls back to `today` instead of trusting a guess.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {
    m: i + 1
    for i, names in enumerate([
        ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
        ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
        ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
        ("december", "dec"),
    ])
    for m in names
}
_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
              "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _num(s: str) -> int:
    return int(s) if s.isdigit() else _NUM_WORDS[s]


def resolve(phrase: str | None, today: date) -> date | None:
    if not phrase:
        return None
    p = phrase.strip().lower().rstrip(".!")

    # ISO date anywhere in the phrase
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", p):
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None

    if p in ("today", "this morning", "tonight"):
        return today
    if p == "yesterday":
        return today - timedelta(days=1)
    if p == "tomorrow":
        return today + timedelta(days=1)

    # "last saturday", "this past saturday", "on saturday" -> most recent past one
    if m := re.fullmatch(r"(?:on |last |this past |past )?(\w+day)", p):
        if m[1] in WEEKDAYS:
            back = (today.weekday() - WEEKDAYS.index(m[1])) % 7 or 7
            return today - timedelta(days=back)
    # "next saturday" -> the coming one
    if m := re.fullmatch(r"(?:next |this coming |coming )(\w+day)", p):
        if m[1] in WEEKDAYS:
            ahead = (WEEKDAYS.index(m[1]) - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead)

    # "3 days ago", "two weeks ago"
    if m := re.fullmatch(r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)"
                         r" (day|week)s? ago", p):
        n = _num(m[1]) * (7 if m[2] == "week" else 1)
        return today - timedelta(days=n)

    # "november 1", "nov 1st", "1 november", optional year
    m = (re.fullmatch(r"(?:on )?([a-z]+)\.? (\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?", p)
         or None)
    if m and m[1] in MONTHS:
        month, day, year = MONTHS[m[1]], int(m[2]), m[3]
    elif (m := re.fullmatch(r"(?:on )?(?:the )?(\d{1,2})(?:st|nd|rd|th)? (?:of )?([a-z]+)"
                            r"(?:,? (\d{4}))?", p)) and m[2] in MONTHS:
        month, day, year = MONTHS[m[2]], int(m[1]), m[3]
    else:
        return None  # vague ("next month") or unrecognized
    try:
        if year:
            return date(int(year), month, day)
        # No year: the date in the current year, unless that is more than
        # ~6 months away, in which case the nearer year is meant.
        d = date(today.year, month, day)
        if (d - today).days > 183:
            d = d.replace(year=today.year - 1)
        elif (today - d).days > 183:
            d = d.replace(year=today.year + 1)
        return d
    except ValueError:
        return None


# Phrases worth trying when scanning free text, most specific first.
_SCAN = [
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\b(?:last|this past|next|this coming) (?:mon|tues|wednes|thurs|fri|satur|sun)day\b",
    r"\b(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten) (?:day|week)s? ago\b",
    r"\b(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.? \d{1,2}(?:st|nd|rd|th)?(?:,? \d{4})?\b",
    r"\b(?:yesterday|today|tomorrow)\b",
]


def find_in_text(text: str, today: date) -> date | None:
    """First resolvable date phrase anywhere in `text`, else None."""
    low = text.lower()
    for pat in _SCAN:
        for m in re.finditer(pat, low):
            if (d := resolve(m.group(0), today)) is not None:
                return d
    return None
