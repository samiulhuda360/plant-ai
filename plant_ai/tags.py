"""The tag register: one list of every signal, shared by the simulator, Modbus server, collector, alarms and UI.

Tag names follow ISA-5.1 style (FIT = flow indicating transmitter, AIT = analyser, LIT = level, TT = temperature,
ZT = position, SC = speed control, JT = power). Every measured tag has a Modbus input register; setpoints are
holding registers. Values travel as unsigned 16-bit integers scaled by ``scale``.
"""

from __future__ import annotations

from dataclasses import dataclass

AREAS = {
    "100": "Influent and equalisation",
    "200": "pH correction and coagulation",
    "300": "Aeration",
    "400": "Clarifier",
    "500": "Discharge",
    "900": "Dye-house chemical dispensing",
}


@dataclass(frozen=True)
class Tag:
    name: str
    description: str
    unit: str
    area: str
    scale: int  # register value = round(value * scale)
    lo: float  # engineering range, for the UI and plausibility checks
    hi: float
    kind: str = "analog"  # analog | state | counter


TAGS: list[Tag] = [
    # Area 100: influent from the dye-house and the equalisation tank
    Tag("FIT-101", "Influent flow", "m3/h", "100", 10, 0, 400),
    Tag("AIT-101", "Influent pH", "pH", "100", 100, 0, 14),
    Tag("TT-101", "Influent temperature", "degC", "100", 10, 0, 90),
    Tag("AIT-102", "Influent conductivity", "uS/cm", "100", 1, 0, 30000),
    Tag("AIT-103", "Influent turbidity", "NTU", "100", 10, 0, 4000),
    Tag("LIT-101", "Equalisation tank level", "m", "100", 100, 0, 5),
    Tag("FV-101", "Equalisation outlet valve command", "%", "100", 10, 0, 100),
    Tag("ZT-101", "Equalisation outlet valve position", "%", "100", 10, 0, 100),
    Tag("FIT-102", "Forward flow to treatment", "m3/h", "100", 10, 0, 200),
    # Area 200: pH correction (sulphuric acid) and coagulation (PAC)
    Tag("AIT-201", "pH correction tank pH (control probe)", "pH", "200", 100, 0, 14),
    Tag("AIT-201B", "pH correction tank pH (check probe)", "pH", "200", 100, 0, 14),
    Tag("P-201_SP", "Acid dosing pump rate command", "L/h", "200", 10, 0, 80),
    Tag("P-201_RUN", "Acid dosing pump running", "", "200", 1, 0, 1, "state"),
    Tag("LIT-201", "Acid day tank level", "%", "200", 10, 0, 100),
    Tag("P-202_SP", "Coagulant dosing pump rate command", "L/h", "200", 10, 0, 30),
    Tag("FIT-202", "Coagulant dosing flow", "L/h", "200", 10, 0, 30),
    Tag("P-202_RUN", "Coagulant dosing pump running", "", "200", 1, 0, 1, "state"),
    Tag("LIT-202", "Coagulant day tank level", "%", "200", 10, 0, 100),
    # Area 300: aeration basin and blower
    Tag("AIT-301", "Aeration basin dissolved oxygen", "mg/L", "300", 100, 0, 12),
    Tag("B-301_CMD", "Aeration blower run command", "", "300", 1, 0, 1, "state"),
    Tag("B-301_RUN", "Aeration blower running", "", "300", 1, 0, 1, "state"),
    Tag("SC-301", "Aeration blower speed", "%", "300", 10, 0, 100),
    Tag("JT-301", "Aeration blower power", "kW", "300", 10, 0, 60),
    # Area 400: clarifier
    Tag("AIT-401", "Clarified water turbidity", "NTU", "400", 10, 0, 500),
    # Area 500: final discharge
    Tag("AIT-501", "Discharge pH", "pH", "500", 100, 0, 14),
    Tag("AIT-502", "Discharge COD", "mg/L", "500", 1, 0, 3000),
    Tag("TT-501", "Discharge temperature", "degC", "500", 10, 0, 90),
    Tag("FIT-501", "Discharge flow", "m3/h", "500", 10, 0, 200),
    Tag("JT-001", "Plant total power", "kW", "500", 10, 0, 120),
    # Area 900: the dye-house automatic chemical dispensing station
    Tag("LIT-901", "Soda ash solution tank level", "%", "900", 10, 0, 100),
    Tag("LIT-902", "Salt brine tank level", "%", "900", 10, 0, 100),
    Tag("LIT-903", "Acetic acid tank level", "%", "900", 10, 0, 100),
    Tag("FIT-901", "Dispensing flow", "L/min", "900", 10, 0, 200),
    Tag("DS-900", "Dispensing station state (0 idle, 1 dispensing, 2 hold)", "", "900", 1, 0, 2, "state"),
    Tag("FQ-901", "Batches dispensed today", "", "900", 1, 0, 200, "counter"),
]

SETPOINTS: list[Tag] = [
    Tag("AIC-201_SP", "pH correction setpoint", "pH", "200", 100, 6.0, 8.0),
    Tag("AIC-301_SP", "Dissolved oxygen setpoint", "mg/L", "300", 100, 0.5, 4.0),
    Tag("LIC-101_SP", "Equalisation level setpoint", "m", "100", 100, 1.0, 4.0),
    Tag("CTRL_MODE", "Dosing strategy (0 fixed-rate, 1 adaptive)", "", "200", 1, 0, 1, "state"),
]

BY_NAME: dict[str, Tag] = {t.name: t for t in TAGS + SETPOINTS}
TAG_NAMES: list[str] = [t.name for t in TAGS]

# Modbus layout: input registers 0-1 hold plant time (minutes since the Unix epoch, high word first);
# measured tags follow from register 2 in the order above. Holding registers 0.. carry the setpoints.
IR_TIME = 0
IR_FIRST_TAG = 2
IR_ADDRESS: dict[str, int] = {t.name: IR_FIRST_TAG + i for i, t in enumerate(TAGS)}
HR_ADDRESS: dict[str, int] = {t.name: i for i, t in enumerate(SETPOINTS)}
IR_COUNT = IR_FIRST_TAG + len(TAGS)
HR_COUNT = len(SETPOINTS)

# Discharge consent limits for the fictional mill (used by compliance reports and the dosing evaluation).
CONSENT = {
    "AIT-501": (6.5, 8.5),  # pH band
    "AIT-401": (None, 20.0),  # turbidity, NTU
    "AIT-502": (None, 250.0),  # COD, mg/L
    "TT-501": (None, 38.0),  # temperature, degC
}


def encode(name: str, value: float) -> int:
    tag = BY_NAME[name]
    return max(0, min(65535, round(value * tag.scale)))


def decode(name: str, raw: int) -> float:
    return raw / BY_NAME[name].scale


def in_consent(values: dict[str, float]) -> dict[str, bool]:
    """Per-parameter compliance for one sample of discharge values."""
    out: dict[str, bool] = {}
    for name, (lo, hi) in CONSENT.items():
        v = values[name]
        out[name] = (lo is None or v >= lo) and (hi is None or v <= hi)
    return out
