"""Guards on the assistant's answer. An answer that fails any guard is replaced by the template answer.

1. **Schema** - valid JSON with a summary, likely causes, checks, a handover note and citations.
2. **Citations** - at least one citation, and every citation names an SOP section that was retrieved for this alarm.
3. **No invented readings** - every number in the answer appears in the alarm facts or in the retrieved SOP text.
4. **Never command equipment** - no claims of having operated plant, no control syntax, and no check that is a
   control action (start, stop, open, close, set ...). Control actions belong to the operator, and the assistant
   phrases them as "ask the control room to ...", the way the SOPs do.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .context import numbers_in

REQUIRED = {"summary": str, "likely_causes": list, "checks": list, "handover": str, "citations": list}
CITE_RE = re.compile(r"SOP-\d{2}\s+\d+\.\d+")

ACTION_CLAIM = re.compile(
    r"\b(?:i|we)\s+(?:have\s+|'ve\s+|just\s+)?(?:started|stopped|opened|closed|reset|switched|changed|set|adjusted|"
    r"increased|decreased|restarted|tripped|shut|isolated|bypassed|overrode|written|wrote)\b"
    r"|\b(?:i|we)\s+(?:will|'ll|am going to|shall)\s+(?:start|stop|open|close|set|change|switch|reset|adjust|"
    r"increase|decrease|restart|shut|isolate|bypass|override|write)\b"
    r"|\blet me\s+(?:start|stop|open|close|set|change|switch|reset|adjust|increase|decrease|restart)\b"
    r"|\bhas been (?:started|stopped|opened|closed|reset|switched|set|adjusted) by the assistant\b",
    re.IGNORECASE,
)
CONTROL_SYNTAX = re.compile(r"write_registers?|\bfc\s*(?:5|6|15|16)\b|modbus\s+write|\w+\s*:=\s*\d", re.IGNORECASE)
CONTROL_VERB = re.compile(
    r"^\s*(?:start|stop|open|close|set|switch|reset|restart|increase|decrease|raise|lower|change|bypass|override|"
    r"force|trip|shut|turn)\b",
    re.IGNORECASE,
)


@dataclass
class GuardReport:
    passed: bool
    failures: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)


def parse_answer(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def answer_text(ans: dict) -> str:
    parts = [str(ans.get("summary", "")), str(ans.get("handover", ""))]
    parts += [str(x) for x in ans.get("likely_causes", [])]
    parts += [str(x) for x in ans.get("checks", [])]
    return "\n".join(parts)


def check_answer(ans: dict | None, allowed_refs: set[str], source_numbers: set[float]) -> GuardReport:
    rep = GuardReport(True)

    def fail(name: str, why: str) -> None:
        rep.passed = False
        rep.checks[name] = False
        rep.failures.append(f"{name}: {why}")

    if ans is None:
        fail("schema", "not valid JSON")
        return rep
    for key, typ in REQUIRED.items():
        if not isinstance(ans.get(key), typ):
            fail("schema", f"missing or wrong type: {key}")
            return rep
    rep.checks["schema"] = True

    cites = [CITE_RE.search(str(c)) for c in ans["citations"]]
    refs = [re.sub(r"\s+", " ", c.group(0)) for c in cites if c]
    if not refs:
        fail("citations", "no SOP section cited")
    elif bad := [r for r in refs if r not in allowed_refs]:
        fail("citations", f"cites sections that were not retrieved: {', '.join(bad)}")
    else:
        rep.checks["citations"] = True

    text = answer_text(ans)
    invented = sorted(n for n in numbers_in(text) if not _known(n, source_numbers))
    if invented:
        fail("numbers", f"numbers not found in the facts or SOPs: {', '.join(_fmt(n) for n in invented[:6])}")
    else:
        rep.checks["numbers"] = True

    problems = []
    if m := ACTION_CLAIM.search(text):
        problems.append(f"claims an action: '{m.group(0)}'")
    if m := CONTROL_SYNTAX.search(text):
        problems.append(f"control syntax: '{m.group(0)}'")
    for c in ans["checks"]:
        if CONTROL_VERB.match(str(c)):
            problems.append(f"check is a control action: '{str(c)[:60]}'")
    if problems:
        fail("no_command", "; ".join(problems))
    else:
        rep.checks["no_command"] = True
    return rep


def _known(n: float, pool: set[float]) -> bool:
    return any(abs(n - p) <= max(0.051, abs(p) * 0.01) for p in pool)


def _fmt(n: float) -> str:
    return f"{n:g}"
