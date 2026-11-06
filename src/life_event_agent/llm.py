"""Swappable model providers.

LLM_PROVIDER=anthropic | ollama | rules
`rules` is a deterministic keyword classifier with no network calls, so
the graph, tests and evals run anywhere (CI included). Real providers
use structured output against the same Pydantic schema.
"""
from __future__ import annotations

import os
import re
from datetime import date
from functools import lru_cache

from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, Field, ValidationError

from . import dates
from .state import Classification, EventType

CLASSIFY_SYSTEM = """\
You classify HR life-event requests from employees.

Event types:
- birth_adoption: a child born, adopted or placed (twins, newborn, "our baby arrived")
- marriage: getting married or domestic partnership ("tied the knot")
- divorce: divorce, legal separation, ending a domestic partnership
- relocation: moving or relocating home address, past OR planned, any timeframe
- medical_leave: the employee's own surgery, illness or recovery needing time off
- unknown: not a life event (IT, payroll, general questions) or truly unclear

Timing never makes an event unknown. "Relocating next month" is relocation.

date_text: copy the words that say WHEN the event happens, exactly as the
employee wrote them ("2026-11-01", "last Saturday", "yesterday", "Nov 3rd",
"next month"). Do not convert or calculate dates. null if no timing is given.

confidence: 0-1. rationale: one short line."""


class _LLMOutput(BaseModel):
    """What the model returns. Dates stay as text; code resolves them."""

    event_type: EventType
    # Required (but nullable): with a default, constrained decoding treats the
    # field as optional and small models just skip it.
    date_text: str | None = Field(description="timing words, verbatim, or null")
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


def provider() -> str:
    return os.getenv("LLM_PROVIDER", "rules").lower()


@lru_cache(maxsize=1)
def _chat_model():
    p = provider()
    if p == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if not os.getenv("ANTHROPIC_API_KEY", "").strip():
            # An empty key surfaces as an opaque "Illegal header value
            # b'Bearer '" connection error, so fail early and clearly.
            raise RuntimeError(
                "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty. "
                "Set it in .env or export it in your shell."
            )
        # Newer Claude models reject non-default temperature, so don't set it.
        # Determinism comes from the schema + evals, not sampling params.
        return ChatAnthropic(model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    if p == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=os.getenv("OLLAMA_MODEL", "qwen2.5"), temperature=0)
    raise ValueError(f"No chat model for provider {p!r}")


def classify(request: str, today: str) -> Classification:
    if provider() == "rules":
        return _rules_classify(request)
    # The model never sees TODAY: it only extracts the date phrase, and
    # dates.resolve() does the arithmetic deterministically.
    # json_schema = constrained decoding on both Anthropic and Ollama, so
    # required fields can't be dropped (tool calling let qwen2.5 omit them).
    model = _chat_model().with_structured_output(_LLMOutput, method="json_schema")
    try:
        out = model.invoke([("system", CLASSIFY_SYSTEM), ("user", request)])
    except (ValidationError, OutputParserException) as e:
        # Malformed model output is a routing failure, not a crash: send the
        # request to a human (confidence 0 -> escalate) and say why.
        return Classification(
            event_type="unknown", confidence=0.0,
            rationale=f"classifier output invalid: {type(e).__name__}",
        )
    today_d = date.fromisoformat(today)
    # Safety net: if the model's phrase is missing or unparseable, look for a
    # resolvable date in the request itself before giving up.
    resolved = dates.resolve(out.date_text, today_d) or dates.find_in_text(request, today_d)
    return Classification(
        event_type=out.event_type,
        event_date=resolved.isoformat() if resolved else None,
        date_text=out.date_text,
        confidence=out.confidence,
        rationale=out.rationale,
    )


# --- deterministic fallback -------------------------------------------------

_RULES: list[tuple[str, str]] = [
    ("birth_adoption", r"\b(baby|born|birth|newborn|adopt\w*|daughter|son)\b"),
    ("marriage", r"\b(married|marry|marriage|wedding|spouse|husband|wife)\b"),
    ("divorce", r"\b(divorc\w*|separat\w*)\b"),
    ("relocation", r"\b(mov(e|ed|ing)|relocat\w*|new (state|address))\b"),
    ("medical_leave", r"\b(surgery|medical leave|fmla|recover\w*|hospital\w*)\b"),
]
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _rules_classify(request: str) -> Classification:
    text = request.lower()
    hits = [et for et, pat in _RULES if re.search(pat, text)]
    # divorce beats marriage when both match ("divorced my spouse")
    if "divorce" in hits and "marriage" in hits:
        hits.remove("marriage")
    iso = (m.group(1) if (m := _DATE.search(request)) else None)
    if len(hits) == 1:
        return Classification(
            event_type=hits[0], event_date=iso, date_text=iso, confidence=0.9,
            rationale=f"keyword match: {hits[0]}",
        )
    return Classification(
        event_type="unknown", event_date=iso, date_text=iso, confidence=0.3,
        rationale="no match" if not hits else f"ambiguous: {hits}",
    )
