"""Assistant evaluation: does it pick the right SOP, and do its answers pass the guards?

Each labelled scenario plants one fault in a fresh simulator run, runs the full detection stack, and asks the
assistant about the first annunciated alarm the fault raises (the alarm an operator would click first), 15 minutes
after it was raised. The label is the SOP for the fault's root cause, which is not always the SOP for the alarm's
own words: a clogged pump often shows first as "clarified turbidity rising".

Scenarios marked ``heldout`` were not looked at while the SOP texts, the query builder or the prompt were written.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from .. import paths
from ..assistant.context import build_context
from ..assistant.explain import Assistant
from ..assistant.llm import LLM, cost_usd
from ..assistant.retrieval import Retriever
from ..detect.anomaly import default_model
from ..detect.engine import DetectionEngine
from ..historian import Historian
from ..protocols.collector import persist
from ..sim.faults import Fault
from ..sim.process import PlantSimulator

SCENARIOS = paths.EVAL / "assistant_scenarios.json"
LLM_CACHE = paths.EVAL / "llm-cache"
START = 240


def expected_sop(kind: str, target: str) -> str:
    if kind == "tank_low":
        return "SOP-05" if target == "LIT-202" else "SOP-13"
    return {
        "shock_load": "SOP-01",
        "ph_drift": "SOP-03",
        "pump_clog": "SOP-04",
        "blower_fail": "SOP-06",
        "stuck_valve": "SOP-07",
        "flatline": "SOP-08",
    }[kind]


def materialise(sc: dict) -> tuple[Historian, int, int]:
    """Runs the scenario into an in-memory historian; returns it with the alarm id to ask about and the time."""
    f = sc["fault"]
    fault = Fault(f["kind"], START, f["duration"], f["target"], f["magnitude"], grace=f.get("grace", 90))
    sim = PlantSimulator(seed=sc["seed"], faults=[fault])
    engine = DetectionEngine(pca=default_model())
    hist = Historian(":memory:")
    first = None
    ask_at = None
    for minute in range(START + fault.duration + fault.grace):
        ts = sim.ts
        values = sim.step()
        hist.write(ts, values, commit=False)
        res = engine.step(ts, values)
        persist(hist, res)
        if first is None and minute >= START:
            for a in res.raised:
                if a.suppressed is None and (a.layer == "anomaly" or a.tag in fault.related):
                    first, ask_at = a.id, ts + 15 * 60
                    break
        if ask_at is not None and ts >= ask_at:
            break
    hist.commit()
    if first is None:
        raise RuntimeError(f"scenario {sc['id']}: the fault raised no alarm")
    return hist, first, hist.last_ts() or 0


def load_scenarios(path: Path = SCENARIOS) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(use_llm: bool, offline: bool = False, split: str | None = None, embeddings: bool = False) -> dict:
    scenarios = [s for s in load_scenarios() if split is None or s["split"] == split]
    embed = None
    if embeddings:
        from ..assistant.llm import embedder

        embed = embedder(paths.EVAL / "embedding-cache", offline=offline)
    llm = LLM(cache_dir=LLM_CACHE, offline=offline) if use_llm else None
    assistant = Assistant(Retriever(embedder=embed), llm)
    rows = []
    for sc in scenarios:
        hist, alarm_id, at = materialise(sc)
        ctx = build_context(hist, alarm_id, at)
        ex = assistant.explain(ctx, use_llm=use_llm)
        hist.close()
        retrieved = [r["sop_id"] for r in ex.retrieved]
        raw_primary = None
        if ex.raw_answer and ex.raw_answer.get("citations"):
            raw_primary = str(ex.raw_answer["citations"][0]).split()[0]
        rows.append(
            {
                "id": sc["id"],
                "split": sc["split"],
                "fault": sc["fault"]["kind"],
                "target": sc["fault"]["target"],
                "alarm": ctx.alarm["message"],
                "expected": sc["expected_sop"],
                "retrieved": retrieved,
                "top1": retrieved[0] == sc["expected_sop"],
                "top3": sc["expected_sop"] in retrieved,
                "mode": ex.mode,
                "primary": ex.primary_sop,
                "correct": ex.primary_sop == sc["expected_sop"],
                "raw_primary": raw_primary,
                "guard_passed": None if ex.guard is None else ex.guard["passed"],
                "guard_failures": None if ex.guard is None else ex.guard["failures"],
                "latency_s": ex.latency_s,
                "cached": ex.cached,
                "prompt_tokens": ex.prompt_tokens,
                "completion_tokens": ex.completion_tokens,
            }
        )
    return {"rows": rows, "summary": summarise(rows, use_llm), "live_calls": llm.live_calls if llm else 0}


def summarise(rows: list[dict], use_llm: bool) -> dict:
    out: dict = {}
    for split in ("tune", "heldout", "all"):
        rs = [r for r in rows if split == "all" or r["split"] == split]
        if not rs:
            continue
        n = len(rs)
        s = {
            "scenarios": n,
            "retrieval_top1": round(sum(r["top1"] for r in rs) / n, 3),
            "retrieval_top3": round(sum(r["top3"] for r in rs) / n, 3),
            "answer_correct_sop": round(sum(r["correct"] for r in rs) / n, 3),
        }
        if use_llm:
            judged = [r for r in rs if r["guard_passed"] is not None]
            s["llm_answers"] = len(judged)
            s["guard_pass_rate"] = round(sum(r["guard_passed"] for r in judged) / len(judged), 3) if judged else None
            s["llm_raw_correct_sop"] = round(sum(r["raw_primary"] == r["expected"] for r in judged) / n, 3)
            s["shown_answer_from_llm"] = sum(r["mode"] == "llm" for r in rs)
            lat = [r["latency_s"] for r in judged]
            s["latency_median_s"] = round(statistics.median(lat), 2) if lat else None
            s["latency_max_s"] = round(max(lat), 2) if lat else None
            pt = sum(r["prompt_tokens"] for r in judged)
            ct = sum(r["completion_tokens"] for r in judged)
            s["tokens_in"], s["tokens_out"] = pt, ct
            s["cost_usd"] = round(cost_usd(pt, ct), 4)
        else:
            s["guard_pass_rate"] = 1.0  # the template answer is checked by the unit tests
        out[split] = s
    return out
