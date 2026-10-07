"""The alarm configuration, written like a small alarm rationalisation database (ISA-18.2 "master alarm database").

Every alarm has a priority, a deadband or on/off delay to stop chattering, and a short operator message. Layers:

* ``limit`` - absolute HI/HIHI/LO/LOLO limits. This is the "threshold-only" baseline.
* ``roc`` - rate-of-change alarms.
* ``discrepancy`` - command vs feedback (a motor that does not run, a valve that does not move, a pump that does
  not deliver).
* ``health`` - sensor-health checks: flatline, probe disagreement (drift), stuck feedback, range limits.
* ``anomaly`` - the PCA model (see ``anomaly.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

PRIORITIES = ("Low", "Medium", "High", "Critical")
PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}

THRESHOLD_ONLY = frozenset({"limit"})
FULL = frozenset({"limit", "roc", "discrepancy", "health", "anomaly"})
PCA_FACTOR = 1.3  # alarm when T-squared or SPE exceeds 1.3x its 99.9th percentile on fault-free training runs


@dataclass(frozen=True)
class Rule:
    id: str
    tag: str
    kind: str  # HI HIHI LO LOLO ROC_FALL ROC_RISE DISC FLATLINE DRIFT STUCK RANGE PCA
    layer: str
    priority: str
    message: str
    limit: float = 0.0
    deadband: float = 0.0
    on_delay: int = 2  # consecutive minutes the condition must hold before the alarm raises
    off_delay: int = 2
    window: int = 0  # minutes, for rate-of-change and health checks
    params: dict = field(default_factory=dict)


def _lim(tag: str, kind: str, limit: float, db: float, prio: str, msg: str, on: int = 2) -> Rule:
    return Rule(f"{tag}.{kind}", tag, kind, "limit", prio, msg, limit, db, on_delay=on)


LIMIT_RULES = [
    _lim("FIT-101", "HI", 215, 10, "Low", "Influent flow high"),
    _lim("AIT-101", "HI", 12.6, 0.2, "Low", "Influent pH high"),
    _lim("TT-101", "HI", 56, 2, "Low", "Influent temperature high"),
    _lim("AIT-102", "HI", 11000, 500, "Low", "Influent conductivity high"),
    _lim("AIT-103", "HI", 900, 50, "Low", "Influent turbidity high"),
    _lim("LIT-101", "HI", 3.8, 0.1, "Medium", "Equalisation tank level high"),
    _lim("LIT-101", "HIHI", 4.5, 0.1, "High", "Equalisation tank level very high (overflow risk)"),
    _lim("LIT-101", "LO", 1.2, 0.1, "Medium", "Equalisation tank level low"),
    _lim("AIT-201", "HI", 8.2, 0.1, "Medium", "pH correction tank pH high"),
    _lim("AIT-201", "LO", 6.4, 0.1, "Medium", "pH correction tank pH low"),
    _lim("AIT-201B", "HI", 8.2, 0.1, "Medium", "pH correction tank pH high (check probe)"),
    _lim("AIT-201B", "LO", 6.4, 0.1, "Medium", "pH correction tank pH low (check probe)"),
    _lim("LIT-201", "LO", 20, 2, "Medium", "Acid day tank level low"),
    _lim("LIT-202", "LO", 20, 2, "Medium", "Coagulant day tank level low"),
    _lim("LIT-202", "LOLO", 8, 2, "High", "Coagulant day tank nearly empty"),
    _lim("LIT-901", "LO", 15, 2, "Low", "Soda ash tank level low"),
    _lim("LIT-902", "LO", 15, 2, "Low", "Salt brine tank level low"),
    _lim("LIT-903", "LO", 15, 2, "Low", "Acetic acid tank level low"),
    _lim("AIT-301", "LO", 1.0, 0.2, "Medium", "Aeration dissolved oxygen low"),
    _lim("AIT-301", "LOLO", 0.5, 0.2, "High", "Aeration dissolved oxygen very low"),
    _lim("AIT-401", "HI", 15, 1, "High", "Clarified turbidity high"),
    _lim("AIT-401", "HIHI", 20, 1, "Critical", "Clarified turbidity above discharge consent"),
    _lim("AIT-501", "HI", 8.3, 0.05, "High", "Discharge pH high"),
    _lim("AIT-501", "HIHI", 8.5, 0.05, "Critical", "Discharge pH above discharge consent"),
    _lim("AIT-501", "LO", 6.7, 0.05, "High", "Discharge pH low"),
    _lim("AIT-501", "LOLO", 6.5, 0.05, "Critical", "Discharge pH below discharge consent"),
    _lim("AIT-502", "HI", 220, 5, "High", "Discharge COD high"),
    _lim("AIT-502", "HIHI", 250, 5, "Critical", "Discharge COD above discharge consent"),
    _lim("TT-501", "HI", 36.5, 0.5, "Medium", "Discharge temperature high"),
    _lim("TT-501", "HIHI", 38, 0.5, "Critical", "Discharge temperature above discharge consent"),
]

ROC_RULES = [
    Rule(f"{t}.ROC_FALL", t, "ROC_FALL", "roc", "Medium", f"{d} falling fast", 14.0, window=60, on_delay=3)
    for t, d in [
        ("LIT-201", "Acid day tank level"),
        ("LIT-202", "Coagulant day tank level"),
        ("LIT-901", "Soda ash tank level"),
        ("LIT-902", "Salt brine tank level"),
        ("LIT-903", "Acetic acid tank level"),
    ]
] + [
    Rule("AIT-301.ROC_FALL", "AIT-301", "ROC_FALL", "roc", "High", "Dissolved oxygen falling fast", 1.0, window=5),
    Rule("AIT-401.ROC_RISE", "AIT-401", "ROC_RISE", "roc", "Medium", "Clarified turbidity rising", 6.0, window=30),
]

DISCREPANCY_RULES = [
    Rule(
        "B-301.DISC",
        "B-301_RUN",
        "DISC",
        "discrepancy",
        "High",
        "Blower B-301 commanded on but not running",
        on_delay=1,
    ),
    Rule(
        "P-202.LOWFLOW",
        "FIT-202",
        "DISC",
        "discrepancy",
        "Medium",
        "Coagulant pump P-202 delivering less than commanded",
        0.7,
        on_delay=10,
        params={"command": "P-202_SP"},
    ),
    Rule(
        "FV-101.DISC",
        "ZT-101",
        "DISC",
        "discrepancy",
        "Medium",
        "Valve FV-101 position does not follow command",
        8.0,
        on_delay=3,
        params={"command": "FV-101"},
    ),
]

FLATLINE_TAGS = [
    "FIT-101",
    "AIT-101",
    "TT-101",
    "AIT-102",
    "AIT-103",
    "LIT-101",
    "FIT-102",
    "AIT-201",
    "AIT-201B",
    "AIT-301",
    "AIT-401",
    "AIT-501",
    "AIT-502",
    "TT-501",
]

HEALTH_RULES = [
    Rule(f"{t}.FLATLINE", t, "FLATLINE", "health", "Medium", f"{t} signal frozen (flatline)", window=30, on_delay=1)
    for t in FLATLINE_TAGS
] + [
    Rule(
        "AIT-201.DRIFT", "AIT-201", "DRIFT", "health", "High", "pH probes AIT-201 and AIT-201B disagree", 0.3, window=10
    ),
    Rule("ZT-101.STUCK", "ZT-101", "STUCK", "health", "Medium", "Valve FV-101 feedback not moving", 0.5, window=15),
    Rule(
        "AIT-101.RANGE", "AIT-101", "RANGE", "health", "Low", "Influent pH analyser at range limit", 13.95, on_delay=1
    ),
]

ANOMALY_RULES = [
    Rule(
        "PCA.SPE",
        "PCA",
        "PCA",
        "anomaly",
        "Medium",
        "Unusual plant behaviour (correlation broken)",
        on_delay=5,
        off_delay=10,
    ),
    Rule(
        "PCA.T2",
        "PCA",
        "PCA",
        "anomaly",
        "Medium",
        "Unusual plant behaviour (extreme operating point)",
        on_delay=5,
        off_delay=10,
    ),
]

ALL_RULES = LIMIT_RULES + ROC_RULES + DISCREPANCY_RULES + HEALTH_RULES + ANOMALY_RULES
RULES_BY_ID = {r.id: r for r in ALL_RULES}

# Designed suppression: while the parent alarm is active the child is logged but not annunciated
# (a higher limit supersedes the lower one; a consequence is shown under its cause).
SUPPRESSED_BY: dict[str, tuple[str, ...]] = {
    "AIT-401.HI": ("AIT-401.HIHI",),
    "AIT-501.HI": ("AIT-501.HIHI",),
    "AIT-501.LO": ("AIT-501.LOLO",),
    "AIT-502.HI": ("AIT-502.HIHI",),
    "TT-501.HI": ("TT-501.HIHI",),
    "LIT-101.HI": ("LIT-101.HIHI",),
    "LIT-202.LO": ("LIT-202.LOLO",),
    "AIT-301.LO": ("AIT-301.LOLO", "B-301.DISC"),
    "AIT-301.LOLO": ("B-301.DISC",),
    "AIT-301.ROC_FALL": ("B-301.DISC",),
    "P-202.LOWFLOW": ("LIT-202.LOLO",),
    "ZT-101.STUCK": ("FV-101.DISC",),
    "AIT-201.HI": ("AIT-201.DRIFT",),
    "AIT-201.LO": ("AIT-201.DRIFT",),
    "AIT-201B.HI": ("AIT-201.DRIFT",),
    "AIT-201B.LO": ("AIT-201.DRIFT",),
}

# Tags an operator would look at for each area (used for incident evidence and the assistant's history window).
AREA_TAGS: dict[str, list[str]] = {
    "100": ["FIT-101", "AIT-101", "TT-101", "AIT-102", "AIT-103", "LIT-101", "FV-101", "ZT-101", "FIT-102"],
    "200": ["AIT-201", "AIT-201B", "P-201_SP", "LIT-201", "P-202_SP", "FIT-202", "LIT-202"],
    "300": ["AIT-301", "B-301_CMD", "B-301_RUN", "SC-301", "JT-301"],
    "400": ["AIT-401", "FIT-202", "P-202_SP"],
    "500": ["AIT-501", "AIT-502", "TT-501", "FIT-501"],
    "900": ["LIT-901", "LIT-902", "LIT-903", "DS-900", "FIT-901"],
}
