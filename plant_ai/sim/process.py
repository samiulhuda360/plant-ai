"""A seeded process model of a textile mill's effluent treatment plant and its dye-house dispensing station.

The model runs in one-minute steps. It is deliberately simple (mass balances, first-order lags, a titration curve,
a jar-test dose curve and Monod kinetics) but keeps the cause-and-effect chains that matter for alarms and dosing:
dye dumps raise flow, pH, turbidity, temperature and conductivity; acid and coagulant demand follow them; a
drifting probe misleads the pH loop; a clogged pump starves coagulation; a blower trip crashes dissolved oxygen.

Process path: influent -> equalisation -> pH correction -> coagulation/flocculation -> aeration -> clarifier ->
discharge. See docs/process-model.md for the equations and parameter values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..tags import TAG_NAMES, in_consent
from .control import ACID_EQ_PER_L, COAG_MG_PER_L, Controller, clamp, optimal_coag_dose, ph_from_alk
from .faults import Fault

DEFAULT_START = 1_791_180_000  # 2026-10-05 06:00 UTC, the start of a day shift in plant time

EQ_AREA = 80.0  # m2
PH_TANK_L = 30_000.0
AER_VOL_L = 900_000.0
ACID_TANK_L = 1000.0
COAG_TANK_L = 150.0
DS_TANKS = {"LIT-901": 4000.0, "LIT-902": 8000.0, "LIT-903": 1000.0}  # soda ash, salt brine, acetic acid
DS_DRAW = {"LIT-901": 180.0, "LIT-902": 360.0, "LIT-903": 25.0}  # litres per dyeing batch

NOISE = {
    "FIT-101": 1.2,
    "AIT-101": 0.03,
    "TT-101": 0.15,
    "AIT-102": 40.0,
    "AIT-103": 4.0,
    "LIT-101": 0.005,
    "ZT-101": 0.03,
    "FIT-102": 0.6,
    "AIT-201": 0.02,
    "AIT-201B": 0.02,
    "FIT-202": 0.05,
    "AIT-301": 0.03,
    "JT-301": 0.2,
    "AIT-401": 0.25,
    "AIT-501": 0.02,
    "AIT-502": 2.0,
    "TT-501": 0.1,
    "FIT-501": 0.6,
    "JT-001": 0.3,
    "LIT-201": 0.05,
    "LIT-202": 0.1,
    "LIT-901": 0.1,
    "LIT-902": 0.1,
    "LIT-903": 0.1,
}


@dataclass
class Stream:
    flow: float  # m3/h
    alk: float  # meq/L
    turb: float  # NTU
    temp: float  # degC
    cond: float  # uS/cm
    cod: float  # mg/L


def lag(x: float, target: float, tau_min: float) -> float:
    return x + (target - x) / tau_min


@dataclass
class PlantSimulator:
    seed: int = 0
    strategy: str = "adaptive"
    faults: list[Fault] = field(default_factory=list)
    start_ts: int = DEFAULT_START
    dump_rate: float = 1.0 / 75.0  # dyeing batches per minute (mean interval 75 min)
    warmup: int = 240  # minutes run before t=0 so every run starts from a settled plant

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed)
        self.t = 0
        self.ctrl = Controller(strategy=self.strategy)
        # process states
        self.eq_level = 2.5
        self.eq = Stream(55.0, 1.0, 150.0, 33.0, 4000.0, 800.0)
        self.valve_pos = 40.0
        self.ph_alk = 0.0
        self.floc_turb = 150.0
        self.floc_cod = 800.0
        self.floc_alk = 0.0
        self.dose_mgl = 70.0
        self.set_turb = 10.0
        self.clar_turb = 10.0
        self.dis_alk = 0.0
        self.aer_cod = 90.0
        self.aer_temp = 31.0
        self.dis_temp = 30.0
        self.do = 2.0
        self.blower_run = 1
        self.tanks = {"LIT-201": 70.0, "LIT-202": 70.0, "LIT-901": 70.0, "LIT-902": 70.0, "LIT-903": 70.0}
        self.refilling = dict.fromkeys(self.tanks, False)
        # influent disturbances (AR(1) states) and the dye-house batch queue
        self.ar = {"flow": 0.0, "alk": 0.0, "turb": 0.0, "cod": 0.0}
        self.dumps: list[tuple[int, int, Stream]] = []  # (start, end, stream)
        self.next_batch = int(self.rng.exponential(1 / self.dump_rate))
        self.dispense_until = -1
        self.batches_today = 0
        self.ds_state = 0
        # sensor-fault memory
        self.frozen: dict[str, float] = {}
        self.last: dict[str, float] = {}
        # totals for the dosing evaluation
        self.acid_l = 0.0
        self.coag_l = 0.0
        self.blower_kwh = 0.0
        self.plant_kwh = 0.0
        self.minutes = 0
        self.out_minutes: dict[str, int] = {"any": 0, "AIT-501": 0, "AIT-401": 0, "AIT-502": 0, "TT-501": 0}
        self.truth: dict[str, float] = {}
        self.measured: dict[str, float] = {}
        self._cmd = self.ctrl.cmd
        if self.warmup:
            self.t = -self.warmup
            self.next_batch -= self.warmup
            for _ in range(self.warmup):
                self.step()
            self._reset_totals()

    def _reset_totals(self) -> None:
        self.acid_l = self.coag_l = self.blower_kwh = self.plant_kwh = 0.0
        self.minutes = 0
        self.out_minutes = dict.fromkeys(self.out_minutes, 0)

    # ------------------------------------------------------------------ public API
    @property
    def ts(self) -> int:
        return self.start_ts + self.t * 60

    def inject(self, fault: Fault) -> None:
        """Adds a fault that starts at the current minute (used by the live training panel)."""
        fault.start = self.t
        self.faults.append(fault)

    def active(self, kind: str) -> Fault | None:
        for f in self.faults:
            if f.kind == kind and f.active(self.t):
                return f
        return None

    def step(self) -> dict[str, float]:
        """Advances one minute and returns the measured tag values."""
        cmd = self._cmd
        inf = self._influent()
        self._dispensing()
        self._equalisation(inf, cmd)
        self._ph_tank(cmd)
        self._coagulation(cmd)
        self._aeration(cmd)
        self._clarifier_and_discharge()
        self._chemical_tanks()
        m = self._measure(inf, cmd)
        self._account(m, cmd)
        self._cmd = self.ctrl.update(m)
        self.t += 1
        if (self.ts - 6 * 3600) % 86400 == 0:  # dispensing counter resets at the 06:00 shift change
            self.batches_today = 0
        return m

    # ------------------------------------------------------------------ process units
    def _influent(self) -> Stream:
        r = self.rng
        hour = (self.ts / 3600.0) % 24
        a = self.ar
        a["flow"] = 0.97 * a["flow"] + r.normal(0, 0.8)
        a["alk"] = 0.98 * a["alk"] + r.normal(0, 0.03)
        a["turb"] = 0.97 * a["turb"] + r.normal(0, 3.0)
        a["cod"] = 0.98 * a["cod"] + r.normal(0, 10.0)
        base = Stream(
            flow=max(10.0, 55.0 + 12.0 * math.sin(2 * math.pi * (hour - 10.0) / 24.0) + a["flow"]),
            alk=max(0.2, 1.0 + a["alk"]),
            turb=max(40.0, 150.0 + a["turb"]),
            temp=33.0 + 1.5 * math.sin(2 * math.pi * (hour - 14.0) / 24.0),
            cond=4000.0 + 30.0 * a["flow"],
            cod=max(300.0, 800.0 + a["cod"]),
        )
        parts = [base]
        self.dumps = [d for d in self.dumps if d[1] > self.t]
        parts += [s for (s0, _, s) in self.dumps if s0 <= self.t]
        shock = self.active("shock_load")
        if shock is not None:
            k = shock.magnitude
            parts.append(Stream(150.0 * k, 20.0 * k, 2000.0 * k, 72.0, 19000.0 * k, 6000.0 * k))
        q = sum(p.flow for p in parts)
        return Stream(
            flow=q,
            alk=sum(p.flow * p.alk for p in parts) / q,
            turb=sum(p.flow * p.turb for p in parts) / q,
            temp=sum(p.flow * p.temp for p in parts) / q,
            cond=sum(p.flow * p.cond for p in parts) / q,
            cod=sum(p.flow * p.cod for p in parts) / q,
        )

    def _dispensing(self) -> None:
        """Dye-house dispensing: each batch draws chemicals now and drains its spent dye bath hours later."""
        r = self.rng
        if self.t >= self.next_batch:
            if self.tanks["LIT-901"] < 5.0:  # no soda ash: the station holds the batch
                self.ds_state = 2
                self.next_batch = self.t + 10
            else:
                self.dispense_until = self.t + 6
                for name, litres in DS_DRAW.items():
                    self.tanks[name] -= litres / DS_TANKS[name] * 100.0
                self.batches_today += 1
                cycle = int(r.integers(180, 300))
                dur = int(r.integers(12, 20))
                dump = Stream(
                    flow=float(r.uniform(40, 70)),
                    alk=float(r.uniform(4, 7)),
                    turb=float(r.uniform(500, 900)),
                    temp=float(r.uniform(50, 60)),
                    cond=float(r.uniform(9000, 12000)),
                    cod=float(r.uniform(2500, 3500)),
                )
                self.dumps.append((self.t + cycle, self.t + cycle + dur, dump))
                self.next_batch = self.t + max(20, int(r.exponential(1 / self.dump_rate)))
        if self.t < self.dispense_until:
            self.ds_state = 1
        elif self.ds_state == 1 or self.ds_state == 2 and self.tanks["LIT-901"] >= 5.0:
            self.ds_state = 0

    def _equalisation(self, inf: Stream, cmd) -> None:
        stuck = self.active("stuck_valve")
        if stuck is not None:
            if "pos" not in stuck.meta:
                stuck.meta["pos"] = clamp(self.valve_pos + stuck.magnitude, 5.0, 95.0)
            self.valve_pos = stuck.meta["pos"]
        else:
            self.valve_pos += clamp(cmd.valve_cmd - self.valve_pos, -5.0, 5.0)
        self.q_out = self.valve_pos / 100.0 * 140.0 * math.sqrt(max(self.eq_level, 0.0) / 2.5)
        self.eq_level = clamp(self.eq_level + (inf.flow - self.q_out) / 60.0 / EQ_AREA, 0.0, 5.0)
        vol_m3 = EQ_AREA * max(self.eq_level, 0.3)
        frac = min(1.0, inf.flow / 60.0 / vol_m3)
        e = self.eq
        e.alk += frac * (inf.alk - e.alk)
        e.turb += frac * (inf.turb - e.turb)
        e.temp += frac * (inf.temp - e.temp)
        e.cond += frac * (inf.cond - e.cond)
        e.cod += frac * (inf.cod - e.cod)
        e.flow = self.q_out

    def _ph_tank(self, cmd) -> None:
        self.acid_actual = cmd.acid_lph if self.tanks["LIT-201"] > 2.0 else 0.0
        q_lpm = self.q_out * 1000.0 / 60.0
        acid_meq_pm = self.acid_actual / 60.0 * ACID_EQ_PER_L * 1000.0
        self.ph_alk += (q_lpm * (self.eq.alk - self.ph_alk) - acid_meq_pm) / PH_TANK_L
        self.ph_true = ph_from_alk(self.ph_alk)

    def _coagulation(self, cmd) -> None:
        clog = self.active("pump_clog")
        blockage = 0.0
        if clog is not None:
            blockage = clog.magnitude * min(1.0, (self.t - clog.start) / 60.0)
        dry = self.tanks["LIT-202"] <= 1.0
        self.coag_actual = 0.0 if dry else cmd.coag_lph * (1.0 - blockage)
        q_lph = max(self.q_out, 1.0) * 1000.0
        dose = self.coag_actual * COAG_MG_PER_L / q_lph
        self.dose_mgl = lag(self.dose_mgl, dose, 10.0)
        self.floc_turb = lag(self.floc_turb, self.eq.turb, 20.0)
        self.floc_cod = lag(self.floc_cod, self.eq.cod, 20.0)
        self.floc_alk = lag(self.floc_alk, self.ph_alk, 20.0)
        r = self.dose_mgl / optimal_coag_dose(self.floc_turb)
        eff = 0.985 * (1.0 - math.exp(-3.0 * r)) - 0.03 * max(0.0, r - 2.0)
        ph_dev = (ph_from_alk(self.floc_alk) - 7.0) / 1.5
        eff *= 1.0 - 0.12 * min(1.0, ph_dev * ph_dev)
        self.coag_eff = clamp(eff, 0.0, 0.99)

    def _aeration(self, cmd) -> None:
        failed = self.active("blower_fail") is not None
        self.blower_run = int(cmd.blower_cmd == 1 and not failed)
        speed = cmd.blower_speed if self.blower_run else 0.0
        self.blower_kw = (6.0 + 30.0 * speed / 100.0) if self.blower_run else 0.0
        cod_in = self.floc_cod * (1.0 - 0.35 * self.coag_eff)
        self.aer_temp = lag(self.aer_temp, 0.85 * self.eq.temp + 0.15 * 20.0, 240.0)
        tt = self.aer_temp
        do_sat = 14.652 - 0.41022 * tt + 0.007991 * tt**2 - 0.000077774 * tt**3
        rate = 2.8 * self.aer_cod / (150.0 + self.aer_cod) * self.do / (0.5 + self.do)
        q_frac = self.q_out * 1000.0 / 60.0 / AER_VOL_L
        self.aer_cod = max(5.0, self.aer_cod + q_frac * (cod_in - self.aer_cod) - rate)
        our = 1.1 * rate + 0.35
        kla = 0.36 * speed / 100.0
        self.do = clamp(self.do + kla * (do_sat - self.do) - our, 0.0, do_sat)

    def _clarifier_and_discharge(self) -> None:
        settled = self.floc_turb * (1.0 - self.coag_eff)
        self.set_turb = lag(self.set_turb, settled, 45.0)
        hydraulic = 1.0 + 0.6 * max(0.0, self.q_out / 80.0 - 1.0)
        self.clar_turb = 2.0 + self.set_turb * hydraulic
        self.dis_alk = lag(self.dis_alk, self.ph_alk, 120.0)
        self.dis_ph = ph_from_alk(self.dis_alk) + 0.2  # CO2 stripping in the aeration basin
        self.dis_cod = self.aer_cod + 1.2 * self.clar_turb
        self.dis_temp = lag(self.dis_temp, 0.8 * self.eq.temp + 0.2 * 20.0, 120.0)

    def _chemical_tanks(self) -> None:
        self.tanks["LIT-201"] -= self.acid_actual / 60.0 / ACID_TANK_L * 100.0
        self.tanks["LIT-202"] -= self.coag_actual / 60.0 / COAG_TANK_L * 100.0
        low = self.active("tank_low")
        for name in self.tanks:
            blocked = low is not None and low.target == name
            if blocked:
                self.tanks[name] -= low.magnitude / 60.0  # passing drain valve, %/h
                self.refilling[name] = False
            elif self.tanks[name] < 25.0:
                self.refilling[name] = True
            if self.refilling[name]:
                self.tanks[name] += 3.0
                if self.tanks[name] >= 90.0:
                    self.refilling[name] = False
            self.tanks[name] = clamp(self.tanks[name], 0.0, 100.0)

    # ------------------------------------------------------------------ instruments
    def _measure(self, inf: Stream, cmd) -> dict[str, float]:
        truth = {
            "FIT-101": inf.flow,
            "AIT-101": ph_from_alk(inf.alk),
            "TT-101": inf.temp,
            "AIT-102": inf.cond,
            "AIT-103": inf.turb,
            "LIT-101": self.eq_level,
            "FV-101": cmd.valve_cmd,
            "ZT-101": self.valve_pos,
            "FIT-102": self.q_out,
            "AIT-201": self.ph_true,
            "AIT-201B": self.ph_true,
            "P-201_SP": cmd.acid_lph,
            "P-201_RUN": 1.0 if cmd.acid_lph > 0 else 0.0,
            "LIT-201": self.tanks["LIT-201"],
            "P-202_SP": cmd.coag_lph,
            "FIT-202": self.coag_actual,
            "P-202_RUN": 1.0 if cmd.coag_lph > 0 else 0.0,
            "LIT-202": self.tanks["LIT-202"],
            "AIT-301": self.do,
            "B-301_CMD": float(cmd.blower_cmd),
            "B-301_RUN": float(self.blower_run),
            "SC-301": cmd.blower_speed if cmd.blower_cmd else 0.0,
            "JT-301": self.blower_kw,
            "AIT-401": self.clar_turb,
            "AIT-501": self.dis_ph,
            "AIT-502": self.dis_cod,
            "TT-501": self.dis_temp,
            "FIT-501": self.q_out,
            "JT-001": self.blower_kw + 6.0 + 0.05 * self.q_out + (0.4 if self.acid_actual > 0 else 0.0) + 0.4,
            "LIT-901": self.tanks["LIT-901"],
            "LIT-902": self.tanks["LIT-902"],
            "LIT-903": self.tanks["LIT-903"],
            "FIT-901": 75.0 if self.ds_state == 1 else 0.0,
            "DS-900": float(self.ds_state),
            "FQ-901": float(self.batches_today),
        }
        self.truth = truth
        noise = self.rng.normal(0.0, 1.0, len(NOISE))
        m = dict(truth)
        for (name, sigma), z in zip(NOISE.items(), noise, strict=True):
            m[name] = truth[name] + sigma * z
        if m["FIT-202"] < 0.05:
            m["FIT-202"] = 0.0
        drift = self.active("ph_drift")
        if drift is not None:
            m[drift.target] += clamp(drift.magnitude * (self.t - drift.start) / 60.0, -2.0, 2.0)
        flat = self.active("flatline")
        if flat is not None:
            if flat.target not in self.frozen:
                self.frozen[flat.target] = self.last.get(flat.target, m[flat.target])
            m[flat.target] = self.frozen[flat.target]
        else:
            self.frozen.clear()
        for name in m:
            m[name] = clamp(m[name], 0.0, 1e9)
        self.last = m
        self.measured = m
        return {name: m[name] for name in TAG_NAMES}

    def _account(self, m: dict[str, float], cmd) -> None:
        self.minutes += 1
        self.acid_l += self.acid_actual / 60.0
        self.coag_l += self.coag_actual / 60.0
        self.blower_kwh += self.blower_kw / 60.0
        self.plant_kwh += self.truth["JT-001"] / 60.0
        true_dis = {
            "AIT-501": self.dis_ph,
            "AIT-401": self.clar_turb,
            "AIT-502": self.dis_cod,
            "TT-501": self.dis_temp,
        }

        ok = in_consent(true_dis)
        bad = False
        for name, good in ok.items():
            if not good:
                self.out_minutes[name] += 1
                bad = True
        if bad:
            self.out_minutes["any"] += 1

    def setpoints(self) -> dict[str, float]:
        return {
            "AIC-201_SP": self.ctrl.ph_sp,
            "AIC-301_SP": self.ctrl.do_sp,
            "LIC-101_SP": self.ctrl.level_sp,
            "CTRL_MODE": 1.0 if self.ctrl.strategy == "adaptive" else 0.0,
        }

    def apply_setpoint(self, name: str, value: float) -> None:
        if name == "AIC-201_SP":
            self.ctrl.ph_sp = value
        elif name == "AIC-301_SP":
            self.ctrl.do_sp = value
        elif name == "LIC-101_SP":
            self.ctrl.level_sp = value
        elif name == "CTRL_MODE":
            self.ctrl.strategy = "adaptive" if value >= 0.5 else "fixed"
            self.strategy = self.ctrl.strategy


def run(sim: PlantSimulator, minutes: int) -> tuple[np.ndarray, np.ndarray]:
    """Runs the simulator and returns (timestamps, values[minutes, tags]) in TAG_NAMES order."""
    ts = np.empty(minutes, dtype=np.int64)
    vals = np.empty((minutes, len(TAG_NAMES)))
    for i in range(minutes):
        ts[i] = sim.ts
        m = sim.step()
        vals[i] = [m[n] for n in TAG_NAMES]
    return ts, vals
