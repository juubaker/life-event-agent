"""Swappable model providers.

LLM_PROVIDER=anthropic | ollama | rules
`rules` is a deterministic keyword classifier with no network calls, so
the graph, tests and evals run anywhere (CI included). Real providers
use structured output against the same Pydantic schema.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from .state import Classification

CLASSIFY_SYSTEM = (
    "You classify HR life-event requests. Return the event type, the event "
    "date, a confidence 0-1, and a one-line rationale. Use 'unknown' when the "
    "request is not a qualifying life event or is too ambiguous.\n"
    "event_date rules: set it ONLY when the employee identifies a specific day, "
    "either an explicit date or a relative day that resolves to one ('last "
    "Saturday', 'yesterday'), resolved against TODAY as yyyy-mm-dd. Vague "
    "timing ('next month', 'soon', 'this spring') means event_date is null."
)


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
    # json_schema = constrained decoding on both Anthropic and Ollama, so
    # required fields can't be dropped (tool calling let qwen2.5 omit them).
    model = _chat_model().with_structured_output(Classification, method="json_schema")
    try:
        return model.invoke(
            [("system", CLASSIFY_SYSTEM), ("user", f"TODAY: {today}\n\n{request}")]
        )
    except (ValidationError, OutputParserException) as e:
        # Malformed model output is a routing failure, not a crash: send the
        # request to a human (confidence 0 -> escalate) and say why.
        return Classification(
            event_type="unknown", confidence=0.0,
            rationale=f"classifier output invalid: {type(e).__name__}",
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
    date = (m.group(1) if (m := _DATE.search(request)) else None)
    if len(hits) == 1:
        return Classification(
            event_type=hits[0], event_date=date, confidence=0.9,
            rationale=f"keyword match: {hits[0]}",
        )
    return Classification(
        event_type="unknown", event_date=date, confidence=0.3,
        rationale="no match" if not hits else f"ambiguous: {hits}",
    )
