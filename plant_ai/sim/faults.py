"""Planted faults with ground truth.

Each fault has a start minute, a duration and a target tag. The detection evaluation scores alerts against these
windows; ``related`` lists the tags whose alerts count as detecting that fault (plant-wide anomaly alerts are
scored by time instead).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

FAULT_KINDS: dict[str, str] = {
    "ph_drift": "pH probe drifting",
    "pump_clog": "Coagulant dosing pump clogging",
    "tank_low": "Chemical day tank running low",
    "shock_load": "Influent shock load (dye batch dump)",
    "blower_fail": "Aeration blower failure",
    "stuck_valve": "Equalisation outlet valve stuck",
    "flatline": "Sensor flatline (frozen signal)",
}

FLATLINE_TARGETS = ("AIT-301", "AIT-401", "FIT-101")
TANK_TARGETS = ("LIT-202", "LIT-901")
DRIFT_TARGETS = ("AIT-201", "AIT-201", "AIT-201B")  # the control probe is the usual culprit

SHOCK_TAGS = {
    "FIT-101",
    "AIT-101",
    "TT-101",
    "AIT-102",
    "AIT-103",
    "LIT-101",
    "FIT-102",
    "AIT-201",
    "AIT-201B",
    "P-201_SP",
    "LIT-201",
    "LIT-202",
    "P-202_SP",
    "FIT-202",
    "AIT-301",
    "SC-301",
    "AIT-401",
    "AIT-501",
    "AIT-502",
    "TT-501",
}


@dataclass
class Fault:
    kind: str
    start: int  # minute index into the run
    duration: int  # minutes
    target: str = ""
    magnitude: float = 0.0
    grace: int = 90  # minutes after the end in which an alert still counts as detecting it
    meta: dict = field(default_factory=dict)

    @property
    def end(self) -> int:
        return self.start + self.duration

    def active(self, t: int) -> bool:
        return self.start <= t < self.end

    @property
    def related(self) -> set[str]:
        k, tgt = self.kind, self.target
        if k == "ph_drift":
            return {"AIT-201", "AIT-201B", "AIT-501", "P-201_SP"}
        if k == "pump_clog":
            return {"FIT-202", "P-202_SP", "AIT-401", "AIT-502"}
        if k == "tank_low":
            return {tgt, "FIT-202", "AIT-401", "DS-900", "FIT-901"}
        if k == "shock_load":
            return set(SHOCK_TAGS)
        if k == "blower_fail":
            return {"B-301_RUN", "B-301_CMD", "AIT-301", "JT-301", "SC-301", "AIT-502"}
        if k == "stuck_valve":
            return {"ZT-101", "FV-101", "LIT-101", "FIT-102"}
        if k == "flatline":
            return {tgt}
        raise ValueError(k)

    def describe(self) -> str:
        base = FAULT_KINDS[self.kind]
        return f"{base} on {self.target}" if self.target else base


def random_fault(kind: str, start: int, rng: np.random.Generator) -> Fault:
    """A fault of the given kind with randomised severity and duration."""
    if kind == "ph_drift":
        rate = float(rng.uniform(0.5, 0.9)) * (1 if rng.random() < 0.5 else -1)
        return Fault(kind, start, int(rng.integers(150, 211)), str(rng.choice(DRIFT_TARGETS)), rate)
    if kind == "pump_clog":
        return Fault(kind, start, int(rng.integers(150, 211)), "P-202", float(rng.uniform(0.6, 0.9)))
    if kind == "tank_low":
        return Fault(
            kind, start, int(rng.integers(240, 301)), str(rng.choice(TANK_TARGETS)), float(rng.uniform(15, 30))
        )
    if kind == "shock_load":
        return Fault(kind, start, int(rng.integers(30, 46)), "FIT-101", float(rng.uniform(0.8, 1.2)), grace=180)
    if kind == "blower_fail":
        return Fault(kind, start, int(rng.integers(45, 121)), "B-301")
    if kind == "stuck_valve":
        delta = float(rng.uniform(12, 25)) * (1 if rng.random() < 0.5 else -1)
        return Fault(kind, start, int(rng.integers(120, 241)), "FV-101", delta)
    if kind == "flatline":
        return Fault(kind, start, int(rng.integers(120, 241)), str(rng.choice(FLATLINE_TARGETS)))
    raise ValueError(kind)


def schedule(seed: int, minutes: int, warmup: int = 360, gap: tuple[int, int] = (240, 480)) -> list[Fault]:
    """One of each fault kind, in random order, separated by fault-free gaps (a multi-day evaluation run)."""
    rng = np.random.default_rng(10_000 + seed)
    kinds = list(FAULT_KINDS)
    rng.shuffle(kinds)
    faults: list[Fault] = []
    t = warmup
    for kind in kinds:
        f = random_fault(kind, t, rng)
        if f.end + f.grace >= minutes:
            break
        faults.append(f)
        t = f.end + f.grace + int(rng.integers(*gap))
    return faults
