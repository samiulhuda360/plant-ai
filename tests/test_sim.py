from __future__ import annotations

import numpy as np

from plant_ai.sim.control import alk_from_ph, optimal_coag_dose, ph_from_alk
from plant_ai.sim.faults import FAULT_KINDS, Fault, schedule
from plant_ai.sim.process import PlantSimulator, run
from plant_ai.tags import TAG_NAMES


def col(vals: np.ndarray, tag: str) -> np.ndarray:
    return vals[:, TAG_NAMES.index(tag)]


def test_seeded_runs_are_reproducible():
    _, a = run(PlantSimulator(seed=5), 200)
    _, b = run(PlantSimulator(seed=5), 200)
    _, c = run(PlantSimulator(seed=6), 200)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_titration_curve_is_monotonic_and_invertible():
    alks = np.linspace(-2, 6, 50)
    phs = [ph_from_alk(a) for a in alks]
    assert all(x < y for x, y in zip(phs, phs[1:], strict=False))
    assert ph_from_alk(0.0) == 7.0
    for ph in (5.0, 7.3, 10.0):
        assert abs(ph_from_alk(alk_from_ph(ph)) - ph) < 1e-9
    assert optimal_coag_dose(400) > optimal_coag_dose(100)


def test_normal_operation_stays_in_consent_with_adaptive_control():
    sim = PlantSimulator(seed=2)
    _, v = run(sim, 1440)
    assert sim.out_minutes["any"] == 0
    assert 6.9 < col(v, "AIT-201").mean() < 7.7
    assert abs(np.median(col(v, "AIT-301")) - 2.0) < 0.2


def test_blower_failure_crashes_dissolved_oxygen():
    sim = PlantSimulator(seed=1, faults=[Fault("blower_fail", 60, 60, "B-301")])
    _, v = run(sim, 130)
    assert col(v, "B-301_RUN")[70] == 0 and col(v, "B-301_CMD")[70] == 1
    assert col(v, "AIT-301")[75] < 0.5
    assert col(v, "B-301_RUN")[125] == 1


def test_pump_clog_starves_coagulant_flow():
    sim = PlantSimulator(seed=1, faults=[Fault("pump_clog", 30, 120, "P-202", 0.8)])
    _, v = run(sim, 140)
    assert col(v, "FIT-202")[120] < 0.4 * col(v, "P-202_SP")[120]


def test_probe_drift_moves_one_probe_only():
    sim = PlantSimulator(seed=1, faults=[Fault("ph_drift", 30, 120, "AIT-201", 0.8)])
    _, v = run(sim, 140)
    gap = col(v, "AIT-201")[130] - col(v, "AIT-201B")[130]
    assert gap > 1.0  # the control probe reads high, so the loop over-doses acid and the true pH falls


def test_flatline_freezes_the_signal_and_stuck_valve_freezes_position():
    faults = [Fault("flatline", 30, 60, "AIT-401"), Fault("stuck_valve", 100, 60, "FV-101", 20)]
    sim = PlantSimulator(seed=1, faults=faults)
    _, v = run(sim, 170)
    assert np.ptp(col(v, "AIT-401")[32:88]) == 0
    assert np.ptp(col(v, "ZT-101")[102:158]) < 0.5  # only transmitter noise
    assert np.ptp(col(v, "FV-101")[102:158]) > 0


def test_tank_low_and_shock_load():
    sim = PlantSimulator(seed=1, faults=[Fault("tank_low", 10, 120, "LIT-202", 30.0)])
    _, v = run(sim, 130)
    assert col(v, "LIT-202")[120] < col(v, "LIT-202")[10] - 40
    sim = PlantSimulator(seed=1, faults=[Fault("shock_load", 10, 30, "FIT-101", 1.0)])
    _, v = run(sim, 40)
    assert col(v, "FIT-101")[20] > 150 and col(v, "AIT-101")[20] > 13


def test_schedule_plants_every_fault_kind_without_overlap():
    faults = schedule(3, 4 * 1440)
    assert {f.kind for f in faults} == set(FAULT_KINDS)
    for a, b in zip(faults, faults[1:], strict=False):
        assert a.end + a.grace < b.start


def test_adaptive_control_uses_less_coagulant_and_blower_energy_than_fixed():
    fixed = PlantSimulator(seed=4, strategy="fixed")
    adaptive = PlantSimulator(seed=4, strategy="adaptive")
    run(fixed, 720)
    run(adaptive, 720)
    assert adaptive.coag_l < fixed.coag_l
    assert adaptive.blower_kwh < fixed.blower_kwh
