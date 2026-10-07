"""Dosing and process control strategies.

Two strategies run on the same plant and read only *measured* tags, as a PLC would:

* ``fixed`` - the baseline found on many older plants: the acid pump runs at a fixed rate under on/off control with
  a deadband, the coagulant pump runs at a constant rate sized for the worst dye dumps, and the aeration blower runs
  at full speed.
* ``adaptive`` - a soft sensor tracks the equalisation tank contents from the influent analysers; acid is dosed by
  feed-forward from that estimate plus a PI trim on the control pH probe; coagulant is paced on flow and estimated
  turbidity with a slow feedback trim from the clarifier turbidity; the blower runs a PI loop on dissolved oxygen.

The equalisation level loop is the same in both.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

ACID_EQ_PER_L = 7.5  # equivalents of acid per litre of the 30 % sulphuric acid
COAG_MG_PER_L = 1.2e6  # mg of PAC product per litre
PH_SCALE = 0.4  # buffer constant of the titration curve, meq/L


def ph_from_alk(a: float) -> float:
    """Titration curve: net alkalinity (meq/L, negative = acidic) to pH."""
    return max(0.0, min(14.0, 7.0 + 1.7 * math.asinh(a / PH_SCALE)))


def alk_from_ph(ph: float) -> float:
    return PH_SCALE * math.sinh((ph - 7.0) / 1.7)


def optimal_coag_dose(turbidity: float) -> float:
    """Jar-test dose curve for the PAC product, mg/L."""
    return 0.25 * turbidity + 25.0


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


@dataclass
class Commands:
    acid_lph: float = 0.0
    coag_lph: float = 4.0
    blower_cmd: int = 1
    blower_speed: float = 60.0
    valve_cmd: float = 40.0


@dataclass
class Controller:
    strategy: str = "adaptive"  # fixed | adaptive
    ph_sp: float = 7.3
    do_sp: float = 2.0
    level_sp: float = 2.5
    # internal states
    acid_on: bool = False
    ph_int: float = 0.0
    do_int: float = 0.0
    lvl_int: float = 0.0
    coag_trim: float = 1.0
    eq_alk: float = 1.0  # soft-sensor estimates of the equalisation tank contents
    eq_turb: float = 150.0
    cmd: Commands = field(default_factory=Commands)

    # fixed-strategy constants
    FIXED_ACID_LPH = 22.0
    FIXED_COAG_LPH = 8.0
    FIXED_BLOWER = 100.0

    def update(self, m: dict[str, float]) -> Commands:
        c = self.cmd
        q_out = max(m["FIT-102"], 1.0)
        level = max(m["LIT-101"], 0.3)

        # Equalisation level: PI on level, biased to pass the average flow.
        err_l = m["LIT-101"] - self.level_sp
        self.lvl_int = clamp(self.lvl_int + err_l * 30.0 / 240.0, -40.0, 60.0)
        c.valve_cmd = clamp(40.0 + 30.0 * err_l + self.lvl_int, 5.0, 100.0)

        # Soft sensor: mix the measured influent into an estimate of the equalisation tank (80 m2 footprint).
        q_in = max(m["FIT-101"], 0.0)
        frac = clamp(q_in / 60.0 / (80.0 * level), 0.0, 1.0)
        self.eq_alk += frac * (alk_from_ph(m["AIT-101"]) - self.eq_alk)
        self.eq_turb += frac * (m["AIT-103"] - self.eq_turb)

        if self.strategy == "fixed":
            ph = m["AIT-201"]
            if ph > self.ph_sp + 0.3:
                self.acid_on = True
            elif ph < self.ph_sp - 0.3:
                self.acid_on = False
            c.acid_lph = self.FIXED_ACID_LPH if self.acid_on else 0.0
            c.coag_lph = self.FIXED_COAG_LPH
            c.blower_cmd = 1
            c.blower_speed = self.FIXED_BLOWER
            return c

        # Acid: feed-forward on the estimated alkalinity load, plus PI trim on the control probe.
        ff = max(0.0, self.eq_alk - alk_from_ph(self.ph_sp)) * q_out * 1000.0 / ACID_EQ_PER_L / 1000.0
        err = m["AIT-201"] - self.ph_sp
        self.ph_int = clamp(self.ph_int + 0.25 * err, -15.0, 25.0)
        c.acid_lph = clamp(ff + 6.0 * err + self.ph_int, 0.0, 80.0)

        # Coagulant: flow- and turbidity-paced dose with a slow trim from the clarifier turbidity.
        target_dose = 1.15 * optimal_coag_dose(self.eq_turb)
        self.coag_trim = clamp(self.coag_trim + 0.002 * (m["AIT-401"] - 10.0), 0.8, 1.6)
        c.coag_lph = clamp(target_dose * self.coag_trim * q_out * 1000.0 / COAG_MG_PER_L, 0.5, 30.0)

        # Aeration: PI on dissolved oxygen.
        err_do = self.do_sp - m["AIT-301"]
        self.do_int = clamp(self.do_int + 1.2 * err_do, -30.0, 60.0)
        c.blower_cmd = 1
        c.blower_speed = clamp(50.0 + 12.0 * err_do + self.do_int, 30.0, 100.0)
        return c
