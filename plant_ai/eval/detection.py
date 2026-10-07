"""Detection evaluation: precision, recall and time to detect per fault, threshold-only vs the full stack.

Each run is four simulated days with all seven fault kinds planted at random times (with random severity, target
and duration) and fault-free gaps between them. An alert counts as a true detection when it is raised inside a
fault's window (start to end plus a grace period) on one of the fault's related tags; plant-wide PCA alerts count
by time alone. Every other annunciated alert is a false alert. Only annunciated alerts (not suppressed) are scored.

Seeds 1-6 were used to set limits, windows and the PCA factor; seeds 101-110 are held out and were not tuned on.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from ..detect.anomaly import default_model
from ..detect.engine import Alert, DetectionEngine
from ..detect.rules import FULL, THRESHOLD_ONLY
from ..sim.faults import FAULT_KINDS, Fault, schedule
from ..sim.process import PlantSimulator

TUNE_SEEDS = tuple(range(1, 7))
HELDOUT_SEEDS = tuple(range(101, 111))
MINUTES = 4 * 1440
CONFIGS = {"threshold": THRESHOLD_ONLY, "full": FULL}


@dataclass
class RunResult:
    seed: int
    config: str
    faults: list[Fault]
    alerts: list[Alert]
    detections: dict[int, int | None]  # fault index -> minutes to detect (None = missed)
    true_alerts: int
    false_alerts: int
    peak_10min: int
    peak_10min_unsuppressed: int
    start_ts: int


def _peak(times: list[int], window_s: int = 600) -> int:
    times = sorted(times)
    best, j = 0, 0
    for i, t in enumerate(times):
        while times[j] <= t - window_s:
            j += 1
        best = max(best, i - j + 1)
    return best


def attribute(alert: Alert, faults: list[Fault], start_ts: int) -> int | None:
    minute = (alert.raised_ts - start_ts) // 60
    for i, f in enumerate(faults):
        if f.start <= minute < f.end + f.grace and (alert.layer == "anomaly" or alert.tag in f.related):
            return i
    return None


def run_case(seed: int, config: str, minutes: int = MINUTES) -> RunResult:
    faults = schedule(seed, minutes)
    sim = PlantSimulator(seed=seed, faults=faults)
    layers = CONFIGS[config]
    engine = DetectionEngine(layers, default_model() if "anomaly" in layers else None)
    for _ in range(minutes):
        ts = sim.ts
        engine.step(ts, sim.step())
    shown = [a for a in engine.alerts if a.suppressed is None]
    detections: dict[int, int | None] = dict.fromkeys(range(len(faults)))
    tp = fp = 0
    for a in shown:
        i = attribute(a, faults, sim.start_ts)
        if i is None:
            fp += 1
            continue
        tp += 1
        ttd = (a.raised_ts - sim.start_ts) // 60 - faults[i].start
        if detections[i] is None or ttd < detections[i]:
            detections[i] = max(0, ttd)
    return RunResult(
        seed,
        config,
        faults,
        engine.alerts,
        detections,
        tp,
        fp,
        _peak([a.raised_ts for a in shown]),
        _peak([a.raised_ts for a in engine.alerts]),
        sim.start_ts,
    )


def summarise(results: list[RunResult]) -> dict:
    by_kind: dict[str, dict] = {}
    for kind in FAULT_KINDS:
        ttds: list[int] = []
        n = hit = 0
        for r in results:
            for i, f in enumerate(r.faults):
                if f.kind != kind:
                    continue
                n += 1
                if r.detections[i] is not None:
                    hit += 1
                    ttds.append(int(r.detections[i]))  # type: ignore[arg-type]
        by_kind[kind] = {
            "faults": n,
            "detected": hit,
            "recall": round(hit / n, 3) if n else None,
            "median_ttd_min": statistics.median(ttds) if ttds else None,
        }
    tp = sum(r.true_alerts for r in results)
    fp = sum(r.false_alerts for r in results)
    n_faults = sum(len(r.faults) for r in results)
    detected = sum(1 for r in results for v in r.detections.values() if v is not None)
    days = len(results) * MINUTES / 1440
    all_ttd = [v for r in results for v in r.detections.values() if v is not None]
    return {
        "runs": len(results),
        "faults": n_faults,
        "detected": detected,
        "recall": round(detected / n_faults, 3) if n_faults else None,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "true_alerts": tp,
        "false_alerts": fp,
        "false_alerts_per_day": round(fp / days, 2),
        "median_ttd_min": statistics.median(all_ttd) if all_ttd else None,
        "peak_alerts_10min": max(r.peak_10min for r in results),
        "peak_alerts_10min_without_suppression": max(r.peak_10min_unsuppressed for r in results),
        "by_fault": by_kind,
    }


def missed(results: list[RunResult]) -> list[dict]:
    out = []
    for r in results:
        for i, f in enumerate(r.faults):
            if r.detections[i] is None:
                out.append({"seed": r.seed, "fault": f.kind, "target": f.target, "magnitude": round(f.magnitude, 2)})
    return out


def evaluate(seeds: tuple[int, ...]) -> dict:
    out: dict = {"seeds": list(seeds), "minutes_per_run": MINUTES}
    for config in CONFIGS:
        results = [run_case(s, config) for s in seeds]
        out[config] = summarise(results)
        out[config]["missed"] = missed(results)
    return out
