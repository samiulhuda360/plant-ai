"""The maintenance assistant: explains an alarm, suggests checks and drafts a shift-handover note.

Two modes share the same retrieval and the same evidence:

* ``template`` (no API key): the top SOP's "Likely causes" and "Immediate checks" sections plus a factual summary
  built from the alarm and its tag history. Always available, deterministic, and passes the guards by design.
* ``llm``: the model reads the alarm facts and the top three SOPs and writes a JSON answer; the guards then check
  it. An answer that fails a guard is discarded and the template answer is shown, with the reason recorded.

The assistant has read access to the historian and the SOPs only. It has no write path to the PLC.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from ..tags import BY_NAME
from .context import AlarmContext, fmt, fmt_ts, numbers_in
from .guards import GuardReport, check_answer, parse_answer
from .llm import LLM, NoModel, cost_usd
from .retrieval import Hit, Retriever

STATE_WORDS = {
    "ACTIVE_UNACK": "still active and not yet acknowledged",
    "ACTIVE_ACK": "still active, acknowledged",
    "RTN_UNACK": "returned to normal, not yet acknowledged",
    "CLEARED": "cleared",
}
USE_SECTIONS = ("Symptoms", "Likely causes", "Immediate checks", "Corrective actions")

SYSTEM = """You are the maintenance assistant for the effluent treatment plant of a textile mill.
You explain an active alarm to the operator using ONLY the alarm facts and the SOP sections provided.
Rules:
- Use only readings, numbers and times that appear in the facts or the SOP text. Never invent or estimate readings.
- Cite the SOP sections you used as "SOP-NN N.N" (for example "SOP-06 6.4"), only from the sections provided.
  Put the SOP that best explains the root cause first.
- You never operate equipment and never claim to have done so. Checks are things the operator verifies or
  inspects. Any control action is phrased as "Ask the control room to ..." or left to the supervisor, as in the SOPs.
- Be brief and specific to this alarm.
Answer with a JSON object with exactly these keys:
{"summary": "2-3 sentences: what is happening and the most likely root cause",
 "likely_causes": ["..."], "checks": ["up to 5 ordered checks"],
 "handover": "2-3 sentences for the shift-handover log", "citations": ["SOP-NN N.N", "..."]}"""


@dataclass
class Explanation:
    mode: str  # template | llm | llm-rejected
    answer: dict
    retrieved: list[dict]
    guard: dict | None = None
    fallback_reason: str | None = None
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached: bool = False
    raw_answer: dict | None = None
    facts: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def primary_sop(self) -> str | None:
        cites = self.answer.get("citations") or []
        return str(cites[0]).split()[0] if cites else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["primary_sop"] = self.primary_sop
        d["cost_usd"] = round(cost_usd(self.prompt_tokens, self.completion_tokens), 6)
        return d


def _bullets(text: str) -> list[str]:
    out, cur = [], ""
    for ln in text.splitlines():
        if ln.startswith("- "):
            if cur:
                out.append(cur.strip())
            cur = ln[2:]
        elif ln.strip():
            cur += " " + ln.strip()
    if cur:
        out.append(cur.strip())
    return out


def _section(hit: Hit, heading: str):
    for s, _ in hit.sections:
        if s.heading.lower().startswith(heading.lower()):
            return s
    return None


def template_answer(ctx: AlarmContext, hits: list[Hit]) -> dict:
    a = ctx.alarm
    top = hits[0]
    causes = _section(top, "Likely causes")
    checks = _section(top, "Immediate checks")
    tag = BY_NAME.get(a["tag"])
    unit = f" {tag.unit}" if tag and tag.unit and a["layer"] == "limit" else ""
    summary = (
        f"{a['priority']} alarm at {fmt_ts(a['raised_ts'])}: {a['message']} "
        f"({a['tag']}, value {fmt(a['value'])}{unit})."
    )

    def spread(t) -> float:
        tag = BY_NAME[t.tag]
        return abs(t.change_30) / max(tag.hi - tag.lo, 1e-9)

    moved = [t for t in sorted(ctx.trends, key=spread, reverse=True) if t.tag != a["tag"] and spread(t) > 0.02]
    if moved:
        t = moved[0]
        u = f" {t.unit}" if t.unit else ""
        summary += (
            f" {t.tag} ({t.description}) changed by {fmt(t.change_30)}{u} over 30 minutes"
            f" and now reads {fmt(t.now)}{u}."
        )
    if ctx.related:
        summary += f" Related alarm: {ctx.related[0]['message']} ({ctx.related[0]['tag']})."
    summary += f" Best matching procedure: {top.sop_id} {top.sop_title}."
    cause_list = _bullets(causes.text) if causes else []
    check_list = _bullets(checks.text) if checks else []
    first = "; ".join(c.rstrip(".") for c in check_list[:2]) or "see the SOP"
    handover = (
        f"{fmt_ts(a['raised_ts'])} {a['message']} ({a['tag']}), {STATE_WORDS.get(a['state'], a['state'])}. "
        f"Follow {top.sop_id} {top.sop_title}. First checks: {first}."
    )
    cites = [s.ref for s in (causes, checks) if s is not None] or [top.sections[0][0].ref]
    return {
        "summary": summary,
        "likely_causes": cause_list,
        "checks": check_list[:5],
        "handover": handover,
        "citations": cites,
    }


def sop_context(hits: list[Hit]) -> tuple[str, set[str], str]:
    """The SOP text shown to the model, the section references it may cite, and the SOP text for number checks."""
    blocks, refs, raw = [], set(), []
    for h in hits:
        blocks.append(f"### {h.sop_id} {h.sop_title}")
        for s, _ in sorted(h.sections, key=lambda x: x[0].section):
            if any(s.heading.startswith(p) for p in USE_SECTIONS):
                blocks.append(f"[{s.ref}] {s.heading}\n{s.text}")
                refs.add(s.ref)
                raw.append(s.text)
    return "\n\n".join(blocks), refs, "\n".join(raw)


class Assistant:
    def __init__(self, retriever: Retriever | None = None, llm: LLM | None = None, k: int = 3) -> None:
        self.retriever = retriever or Retriever()
        self.llm = llm
        self.k = k

    def explain(self, ctx: AlarmContext, use_llm: bool = True) -> Explanation:
        hits = self.retriever.search(ctx.query(), k=self.k)
        retrieved = [{"sop_id": h.sop_id, "title": h.sop_title, "score": round(h.score, 3)} for h in hits]
        facts = ctx.facts_text()
        base = template_answer(ctx, hits)
        if not use_llm or self.llm is None or not self.llm.available:
            return Explanation("template", base, retrieved, facts=facts)

        sop_text, refs, raw = sop_context(hits)
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"ALARM FACTS\n{facts}\n\nSOP SECTIONS\n{sop_text}"},
        ]
        started = time.perf_counter()
        try:
            res = self.llm.chat(messages, json_mode=True)
        except (NoModel, RuntimeError) as e:
            return Explanation("template", base, retrieved, fallback_reason=f"model unavailable: {e}", facts=facts)
        ans = parse_answer(res.content)
        if ans is not None and isinstance(ans.get("citations"), list):
            ans["citations"] = [str(c).strip() for c in ans["citations"]]
        report: GuardReport = check_answer(ans, refs, ctx.numbers() | numbers_in(raw))
        common = {
            "retrieved": retrieved,
            "guard": asdict(report),
            "latency_s": res.latency_s if res.cached else round(time.perf_counter() - started, 3),
            "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens,
            "cached": res.cached,
            "raw_answer": ans,
            "facts": facts,
        }
        if report.passed and ans is not None:
            return Explanation("llm", ans, **common)
        return Explanation("llm-rejected", base, fallback_reason="; ".join(report.failures), **common)
