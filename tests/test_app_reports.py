from __future__ import annotations

import json

from fastapi.testclient import TestClient

from plant_ai import reports
from plant_ai.eval import assistant as asst_eval
from plant_ai.eval import detection, dosing
from plant_ai.historian import Historian
from plant_ai.web.app import create_app


def test_historian_keeps_operator_ack(tmp_path):
    h = Historian(tmp_path / "h.db")
    a = {
        "id": 1, "rule_id": "X.HI", "tag": "AIT-301", "kind": "HI", "layer": "limit", "priority": "Low",
        "message": "m", "raised_ts": 60, "cleared_ts": None, "acked_ts": None, "value": 1.0, "area": "300",
        "suppressed": None, "incident_id": 1, "evidence": {},
    }  # fmt: skip
    h.upsert_alarm(a)
    assert h.ack(1, 120)
    h.upsert_alarm({**a, "cleared_ts": 180})
    row = h.alarm(1)
    assert row["acked_ts"] == 120 and row["cleared_ts"] == 180 and row["state"] == "CLEARED"


def test_shift_boundaries():
    day = 1_791_158_400  # 2026-10-05 00:00 UTC
    assert reports.shift_of(day + 7 * 3600) == ("A", day + 6 * 3600)
    assert reports.shift_of(day + 15 * 3600) == ("B", day + 14 * 3600)
    assert reports.shift_of(day + 23 * 3600) == ("C", day + 22 * 3600)
    assert reports.shift_of(day + 2 * 3600) == ("C", day - 2 * 3600)


def test_reports_from_history(demo_db):
    h = Historian(demo_db)
    last = h.last_ts()
    d = reports.compliance_data(h, last - last % 86400)
    assert d["samples"] > 0 and {p["tag"] for p in d["params"]} == {"AIT-501", "AIT-401", "AIT-502", "TT-501"}
    assert "Daily discharge compliance report" in reports.render_compliance(d)
    _, start = reports.shift_of(last)
    s = reports.shift_data(h, start - 8 * 3600)
    assert s["alarms_annunciated"] >= 1
    assert "Shift report" in reports.render_shift(s)


def test_api_endpoints(demo_db):
    client = TestClient(create_app(demo_db))
    ov = client.get("/api/overview").json()
    assert ov["plant_ts"] and "AIT-301" in ov["values"]
    tr = client.get("/api/trend", params={"tags": "AIT-301,FIT-202", "hours": 2}).json()
    assert len(tr["series"]["AIT-301"]) > 100
    alarms = client.get("/api/alarms").json()
    assert alarms
    first = alarms[-1]["id"]
    assert client.post(f"/api/alarms/{first}/ack").json()["acknowledged"] is True
    ex = client.post(f"/api/assistant/{first}", params={"llm": False}).json()
    assert ex["mode"] == "template" and ex["answer"]["citations"]
    assert client.get("/api/notes").json()
    assert client.get("/api/incidents").json()
    assert client.get("/api/kpis").status_code == 200
    assert "compliance" in client.get("/reports/compliance").text
    assert "Shift report" in client.get("/reports/shift").text
    assert client.post("/api/sim/inject", json={"kind": "blower_fail"}).status_code == 409
    assert client.get("/").status_code == 200


def test_evaluation_pieces_run():
    r = detection.run_case(1, "threshold", minutes=1440)
    assert r.true_alerts + r.false_alerts > 0
    d = dosing.run_strategy("fixed", 1, days=1)
    assert d["acid_l"] > 0 and d["minutes"] == 1440
    scenarios = asst_eval.load_scenarios()
    assert len(scenarios) == 31
    assert sum(s["split"] == "heldout" for s in scenarios) == 10
    assert {s["expected_sop"] for s in scenarios} <= {f"SOP-{i:02d}" for i in range(1, 17)}
    json.dumps(scenarios)
