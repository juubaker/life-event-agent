from datetime import date

import pytest

from life_event_agent.dates import resolve

TODAY = date(2026, 11, 5)  # a Thursday


@pytest.mark.parametrize("phrase,expected", [
    ("2026-11-01", date(2026, 11, 1)),
    ("on 2026-10-15", date(2026, 10, 15)),
    ("yesterday", date(2026, 11, 4)),
    ("today", TODAY),
    ("last Saturday", date(2026, 10, 31)),
    ("this past saturday", date(2026, 10, 31)),
    ("on Saturday", date(2026, 10, 31)),
    ("last Thursday", date(2026, 10, 29)),       # not today
    ("next Monday", date(2026, 11, 9)),
    ("3 days ago", date(2026, 11, 2)),
    ("two weeks ago", date(2026, 10, 22)),
    ("Nov 3rd", date(2026, 11, 3)),
    ("November 1, 2026", date(2026, 11, 1)),
    ("the 3rd of November", date(2026, 11, 3)),
    ("Dec 20", date(2026, 12, 20)),
    ("Jan 10", date(2027, 1, 10)),               # near future crosses the year
    ("July 4", date(2026, 7, 4)),
])
def test_resolves_specific_days(phrase, expected):
    assert resolve(phrase, TODAY) == expected


@pytest.mark.parametrize("phrase", [
    None, "", "next month", "soon", "this spring", "a while back", "2026-02-30",
])
def test_vague_or_invalid_returns_none(phrase):
    assert resolve(phrase, TODAY) is None


def test_llm_phrase_is_resolved_in_code(monkeypatch):
    from life_event_agent import llm

    class FakeModel:
        def with_structured_output(self, schema, **k):
            self.schema = schema
            return self

        def invoke(self, messages):
            assert "TODAY" not in messages[-1][1]  # model never does the math
            return self.schema(event_type="marriage", date_text="last Saturday",
                               confidence=0.95, rationale="tied the knot")

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm, "_chat_model", lambda: FakeModel())
    c = llm.classify("My partner and I tied the knot last Saturday.", "2026-11-05")
    assert c.event_date == "2026-10-31"
    assert c.date_text == "last Saturday"


@pytest.mark.parametrize("text,expected", [
    ("We just had a baby girl on 2026-11-01! What do I need to do?", date(2026, 11, 1)),
    ("My partner and I tied the knot last Saturday.", date(2026, 10, 31)),
    ("Our twins arrived Nov 2nd, two weeks early!", date(2026, 11, 2)),
    ("I'm relocating to Texas next month", None),
])
def test_find_in_text(text, expected):
    from life_event_agent.dates import find_in_text
    assert find_in_text(text, TODAY) == expected


def test_null_date_text_falls_back_to_request(monkeypatch):
    """The exact failure seen with qwen2.5: model returns date_text=None."""
    from life_event_agent import llm

    class NullDateModel:
        def with_structured_output(self, schema, **k):
            self.schema = schema
            return self

        def invoke(self, messages):
            return self.schema(event_type="birth_adoption", date_text=None,
                               confidence=0.9, rationale="baby")

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm, "_chat_model", lambda: NullDateModel())
    c = llm.classify("We had a baby on 2026-11-01", "2026-11-05")
    assert c.event_date == "2026-11-01"


def test_llm_schema_requires_date_text():
    from life_event_agent.llm import _LLMOutput
    assert "date_text" in _LLMOutput.model_json_schema()["required"]
