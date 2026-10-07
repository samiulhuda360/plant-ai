from __future__ import annotations

import numpy as np

from plant_ai.detect.anomaly import PCAModel
from plant_ai.detect.engine import DetectionEngine
from plant_ai.detect.rules import FULL, THRESHOLD_ONLY
from plant_ai.tags import BY_NAME

T0 = 1_791_180_000


def jitter(values: dict, i: int) -> dict:
    """Small changes so that no healthy signal looks frozen."""
    rng = np.random.default_rng(i)
    return {
        k: v + rng.normal(0, 2.0 / BY_NAME[k].scale) if BY_NAME[k].kind == "analog" else v for k, v in values.items()
    }


def test_limit_alarm_has_on_delay_and_deadband(base_values):
    e = DetectionEngine(THRESHOLD_ONLY)
    v = dict(base_values)
    v["AIT-301"] = 0.8  # below the LO limit of 1.0
    r1 = e.step(T0, v).raised
    r2 = e.step(T0 + 60, v).raised
    assert not r1 and [a.rule_id for a in r2] == ["AIT-301.LO"]
    v["AIT-301"] = 1.1  # back above the limit but inside the 0.2 deadband: stays active
    for i in range(3):
        e.step(T0 + 120 + i * 60, v)
    assert "AIT-301.LO" in e.active
    v["AIT-301"] = 1.5
    for i in range(3):
        e.step(T0 + 400 + i * 60, v)
    assert "AIT-301.LO" not in e.active


def test_discrepancy_and_design_suppression(base_values):
    e = DetectionEngine(FULL - {"anomaly"})
    v = dict(base_values)
    v["B-301_RUN"] = 0.0
    v["AIT-301"] = 0.2
    raised = []
    for i in range(4):
        raised += e.step(T0 + i * 60, jitter(v, i) | {"B-301_RUN": 0.0, "B-301_CMD": 1.0, "AIT-301": 0.2}).raised
    by_rule = {a.rule_id: a for a in raised}
    assert by_rule["B-301.DISC"].suppressed is None
    assert by_rule["AIT-301.LOLO"].suppressed == "design"  # a consequence of the blower trip


def test_flatline_check_fires_on_a_frozen_signal(base_values):
    e = DetectionEngine(frozenset({"health"}))
    raised = []
    for i in range(40):
        v = jitter(base_values, i)
        v["AIT-401"] = 9.5  # frozen
        raised += e.step(T0 + i * 60, v).raised
    assert [a.rule_id for a in raised] == ["AIT-401.FLATLINE"]


def test_probe_drift_names_the_suspect_probe(base_values):
    e = DetectionEngine(frozenset({"health"}))
    raised = []
    for i in range(30):
        v = jitter(base_values, i)
        v["AIT-201"] = 7.3 + 0.05 * i  # control probe drifting up
        v["AIT-201B"] = 7.3
        v["AIT-501"] = 7.5
        raised += e.step(T0 + i * 60, v).raised
    drift = [a for a in raised if a.kind == "DRIFT"]
    assert drift and drift[0].tag == "AIT-201"


def test_flood_suppression_keeps_high_priority_alarms(base_values):
    e = DetectionEngine(THRESHOLD_ONLY, flood_limit=3)
    v = dict(base_values)
    v.update({"FIT-101": 300, "AIT-101": 13.5, "TT-101": 70, "AIT-102": 20000, "AIT-103": 2000, "AIT-401": 30})
    raised = []
    for i in range(3):
        raised += e.step(T0 + i * 60, v).raised
    shown = [a for a in raised if a.suppressed is None]
    flooded = [a for a in raised if a.suppressed == "flood"]
    assert flooded and all(a.priority in ("Low", "Medium") for a in flooded)
    assert any(a.rule_id == "AIT-401.HIHI" for a in shown)


def test_incidents_group_alarms_close_in_time(base_values):
    e = DetectionEngine(THRESHOLD_ONLY, incident_group=45, incident_quiet=10)
    v = dict(base_values)
    bad = dict(v, **{"AIT-301": 0.8})
    for i in range(5):
        e.step(T0 + i * 60, bad)
    for i in range(5, 40):
        e.step(T0 + i * 60, v)
    later = dict(v, **{"TT-501": 37.0})
    for i in range(200, 205):
        e.step(T0 + i * 60, later)
    assert len(e.incidents) == 2
    assert e.incidents[0].closed_ts is not None
    assert e.incidents[0].title == "Aeration dissolved oxygen low"


def test_pca_flags_a_broken_correlation():
    rng = np.random.default_rng(0)
    t = rng.normal(size=2000)
    x = np.column_stack([t + rng.normal(0, 0.1, 2000), 2 * t + rng.normal(0, 0.1, 2000), rng.normal(size=2000)])
    m = PCAModel.fit(x, ["a", "b", "c"], explained=0.9)
    _, spe_ok = m.statistics(np.array([1.0, 2.0, 0.0]))
    _, spe_bad = m.statistics(np.array([1.0, -2.0, 0.0]))  # a and b no longer move together
    assert spe_ok[0] < m.spe_limit < spe_bad[0]
    contrib = m.contributions(np.array([1.0, -2.0, 0.0]))
    assert max(contrib, key=contrib.get) in ("a", "b")
    assert PCAModel.from_dict(m.to_dict()).spe_limit == m.spe_limit
