from __future__ import annotations

import json

import pytest

from plant_ai.assistant.context import build_context, numbers_in
from plant_ai.assistant.explain import Assistant, sop_context, template_answer
from plant_ai.assistant.guards import check_answer, parse_answer
from plant_ai.assistant.llm import LLM, ChatResult
from plant_ai.assistant.retrieval import Retriever, load_kb, tokenize
from plant_ai.historian import Historian


@pytest.fixture(scope="module")
def retriever():
    return Retriever()


def test_knowledge_base_has_numbered_sections():
    sections = load_kb()
    sops = {s.sop_id for s in sections}
    assert len(sops) == 16
    assert all(s.text for s in sections)
    assert any(s.ref == "SOP-06 6.4" and s.heading == "Immediate checks" for s in sections)


def test_tokenizer_keeps_instrument_tags():
    toks = tokenize("Blower B-301 tripped; AIT-301 dissolved oxygen falling")
    assert "b-301" in toks and "ait-301" in toks and "oxygen" in toks


@pytest.mark.parametrize(
    ("query", "sop"),
    [
        ("Blower B-301 commanded on but not running", "SOP-06"),
        ("pH probes AIT-201 and AIT-201B disagree", "SOP-03"),
        ("Coagulant pump P-202 delivering less than commanded", "SOP-04"),
        ("AIT-401 signal frozen (flatline)", "SOP-08"),
        ("Valve FV-101 position does not follow command", "SOP-07"),
        ("Soda ash tank level falling fast", "SOP-13"),
    ],
)
def test_bm25_finds_the_procedure(retriever, query, sop):
    assert retriever.search(query)[0].sop_id == sop


def test_numbers_ignore_tags_and_sop_refs():
    assert numbers_in("AIT-301 read 0.42 mg/L at 2026-10-05 10:15, see SOP-06 6.4") == {0.42, 10.0, 15.0}


GOOD = {
    "summary": "Blower B-301 is not running and DO is 0.02 mg/L.",
    "likely_causes": ["Blower motor overload trip"],
    "checks": ["Check the VFD display for the trip reason"],
    "handover": "Blower trip, DO 0.02 mg/L. Ask the control room to start the standby blower.",
    "citations": ["SOP-06 6.4"],
}


def test_guards_pass_a_grounded_answer():
    rep = check_answer(GOOD, {"SOP-06 6.4"}, {0.02})
    assert rep.passed, rep.failures


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        ({"summary": "DO is 0.35 mg/L."}, "numbers"),
        ({"citations": []}, "citations"),
        ({"citations": ["SOP-09 9.4"]}, "citations"),
        ({"handover": "I have restarted the blower."}, "no_command"),
        ({"checks": ["Start the standby blower"]}, "no_command"),
        ({"summary": "Use write_register to set the speed."}, "no_command"),
        ({"checks": "not a list"}, "schema"),
    ],
)
def test_guards_reject_bad_answers(change, failed):
    rep = check_answer({**GOOD, **change}, {"SOP-06 6.4"}, {0.02})
    assert not rep.passed
    assert rep.checks.get(failed) is False


def test_parse_answer_handles_code_fences():
    assert parse_answer("```json\n" + json.dumps(GOOD) + "\n```") == GOOD
    assert parse_answer("not json") is None


def _first_alarm(db, rule: str) -> int:
    hist = Historian(db)
    return next(a["id"] for a in hist.alarms(limit=500) if a["rule_id"] == rule)


def test_template_answer_is_grounded(demo_db, retriever):
    hist = Historian(demo_db)
    ctx = build_context(hist, _first_alarm(demo_db, "B-301.DISC"))
    hits = retriever.search(ctx.query())
    assert hits[0].sop_id == "SOP-06"
    ans = template_answer(ctx, hits)
    _, refs, raw = sop_context(hits)
    rep = check_answer(ans, refs, ctx.numbers() | numbers_in(raw))
    assert rep.passed, rep.failures
    assert "B-301" in ctx.facts_text()


class FakeLLM(LLM):
    """Returns a fixed answer, as if from the model, without any network call."""

    def __init__(self, answer: str) -> None:
        super().__init__(offline=True)
        self.answer = answer

    @property
    def available(self) -> bool:
        return True

    def chat(self, messages, tools=None, temperature=0.0, json_mode=False) -> ChatResult:
        return ChatResult({"content": self.answer}, 100, 50, 0.5, False, "fake")


def test_llm_answer_that_invents_a_reading_falls_back_to_template(demo_db):
    hist = Historian(demo_db)
    ctx = build_context(hist, _first_alarm(demo_db, "B-301.DISC"))
    bad = {**GOOD, "summary": "DO is 3.71 mg/L and the blower is fine."}
    ex = Assistant(llm=FakeLLM(json.dumps(bad))).explain(ctx)
    assert ex.mode == "llm-rejected"
    assert "numbers" in ex.fallback_reason
    assert ex.answer["citations"][0].startswith("SOP-06")


def test_llm_answer_that_passes_is_shown(demo_db):
    hist = Historian(demo_db)
    ctx = build_context(hist, _first_alarm(demo_db, "B-301.DISC"))
    good = {**GOOD, "summary": "Blower B-301 is commanded on but not running.", "handover": "Blower B-301 tripped."}
    ex = Assistant(llm=FakeLLM(json.dumps(good))).explain(ctx)
    assert ex.mode == "llm", ex.fallback_reason
