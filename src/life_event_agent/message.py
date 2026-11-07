"""The employee-facing message: built from facts in code, warmed up by the model.

`build_template` writes a correct, plain message from the plan's facts.
`rewrite` asks the model to make it sound human, then `check_locked`
verifies the result before it is accepted:

- every locked fact (deadline, weeks of leave, notice days) appears verbatim
- no number appears that wasn't in the template
- no month name appears that wasn't in the template

Anything that fails, including a model error, falls back to the template.
The model only ever sees the template, never the employee's own words, so
text in the request (say, a prompt injection) can't reach the message.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .dates import MONTHS

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]

OPENERS = {
    "birth_adoption": "Congratulations on your new arrival.",
    "marriage": "Congratulations on your marriage.",
    "divorce": "Thanks for letting us know about this change.",
    "relocation": "Thanks for letting us know about your move.",
    "medical_leave": "We hope your procedure and recovery go smoothly.",
}

REWRITE_SYSTEM = """\
You rewrite short HR messages to an employee so they read warm, plain and human.

Rules:
- Keep every fact. These exact strings must appear unchanged: {facts}
- Do not add any date, number, deadline, amount, policy or promise that is not in the original.
- No names, no sign-off, no subject line. Under 110 words.
- Reply with the rewritten message only."""

MAX_CHARS = 900


def human(d: date) -> str:
    return f"{MONTH_NAMES[d.month - 1]} {d.day}, {d.year}"


@dataclass
class Draft:
    text: str
    facts: list[str]  # strings the rewrite must keep verbatim


def build_template(event_type: str, today: date, deadline: date | None,
                   leave: dict | None) -> Draft:
    parts, facts = [OPENERS.get(event_type, "Thanks for reaching out.")], []
    if deadline:
        d = human(deadline)
        facts.append(d)
        if deadline < today:
            parts.append(f"The usual window to change your benefits for this event "
                         f"closed on {d}, so HR will follow up with you about your options.")
        else:
            parts.append(f"You have until {d} to make benefit changes for this event.")
    if leave:
        weeks, notice = f"{leave['weeks']} weeks", f"{leave['notice_days']} days"
        facts += [weeks, notice]
        parts.append(f"You can take up to {weeks} of {leave['label'].lower()}. "
                     f"When you can plan ahead, please give {notice} of notice.")
    parts.append("Your checklist below lists each step, and HR will check your "
                 "supporting documents.")
    return Draft(" ".join(parts), facts)


_NUM = re.compile(r"\d+")
_MONTH = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b",
                    re.IGNORECASE)


def check_locked(text: str, draft: Draft) -> str | None:
    """None if `text` keeps every fact and adds none; else the reason."""
    if not text.strip():
        return "empty rewrite"
    if len(text) > MAX_CHARS:
        return f"too long ({len(text)} chars)"
    for f in draft.facts:
        if f not in text:
            return f"dropped fact: {f}"
    if extra := set(_NUM.findall(text)) - set(_NUM.findall(draft.text)):
        return f"new number: {sorted(extra)[0]}"
    def months(s: str) -> set[str]:
        return {MONTHS[m.lower()] for m in _MONTH.findall(s)}
    if months(text) - months(draft.text):
        return "new date"
    return None


def rewrite(draft: Draft, model) -> tuple[str, str, str]:
    """(message, source, note). source is 'llm' or 'template'."""
    if model is None:
        return draft.text, "template", ""
    try:
        reply = model.invoke([
            ("system", REWRITE_SYSTEM.format(facts="; ".join(draft.facts) or "(none)")),
            ("user", draft.text),
        ])
        text = reply.text if isinstance(reply.text, str) else str(reply.content)
        text = text.strip().strip('"')
    except Exception as e:
        return draft.text, "template", f"rewrite failed: {type(e).__name__}"
    if why := check_locked(text, draft):
        return draft.text, "template", f"rewrite rejected: {why}"
    return text, "llm", ""
