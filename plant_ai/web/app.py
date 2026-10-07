"""The dashboard API (FastAPI) and the static single-page HMI.

The API reads the historian. The only writes are operator acknowledgements, assistant notes and, in demo mode,
training-fault injection into the simulator. No endpoint writes to the PLC.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import reports
from ..assistant.context import build_context
from ..assistant.explain import Assistant
from ..assistant.llm import LLM
from ..historian import Historian
from ..sim.faults import FAULT_KINDS
from ..tags import AREAS, CONSENT, SETPOINTS, TAGS

STATIC = Path(__file__).parent / "static"


class InjectRequest(BaseModel):
    kind: str


def create_app(db_path: Path, live=None, assistant: Assistant | None = None) -> FastAPI:
    app = FastAPI(title="plant-ai", version="1.0.0", docs_url="/api/docs")
    hist = Historian(db_path)
    llm = LLM() if os.getenv("AI_API_KEY") else None
    helper = assistant or Assistant(llm=llm)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/meta")
    def meta() -> dict:
        return {
            "tags": [
                {"name": t.name, "description": t.description, "unit": t.unit, "area": t.area, "lo": t.lo, "hi": t.hi}
                for t in TAGS
            ],
            "setpoints": [t.name for t in SETPOINTS],
            "areas": AREAS,
            "consent": {k: list(v) for k, v in CONSENT.items()},
            "faults": FAULT_KINDS if live is not None else {},
            "llm": helper.llm is not None and helper.llm.available,
            "modbus": f"{live.host}:{live.port}" if live is not None else None,
        }

    @app.get("/api/overview")
    def overview() -> dict:
        latest = hist.latest()
        now = hist.last_ts()
        active = [a for a in hist.active_alarms() if a["suppressed"] is None]
        values = {k: v["value"] for k, v in latest.items()}
        consent = {}
        for tag, (lo, hi) in CONSENT.items():
            v = values.get(tag)
            consent[tag] = v is not None and (lo is None or v >= lo) and (hi is None or v <= hi)
        return {
            "plant_ts": now,
            "values": values,
            "active_alarms": active,
            "consent": consent,
            "collector": hist.meta("collector", "backfill only"),
            "mode": "adaptive" if live is None or live.sim.ctrl.strategy == "adaptive" else "fixed",
            "faults": live.active_faults() if live is not None else [],
            "open_incidents": [i for i in hist.incidents(limit=20) if i["status"] == "open"],
        }

    @app.get("/api/trend")
    def trend(tags: str = Query(...), hours: float = 6.0) -> dict:
        end = hist.last_ts() or 0
        start = end - int(hours * 3600)
        return {"start": start, "end": end, "series": {t: hist.series(t, start, end) for t in tags.split(",") if t}}

    @app.get("/api/alarms")
    def alarms(limit: int = 150) -> list[dict]:
        return [a for a in hist.alarms(limit=limit)]

    @app.post("/api/alarms/{alarm_id}/ack")
    def ack(alarm_id: int) -> dict:
        ok = hist.ack(alarm_id, hist.last_ts() or 0)
        return {"acknowledged": ok}

    @app.get("/api/incidents")
    def incidents(limit: int = 30) -> list[dict]:
        out = hist.incidents(limit=limit)
        for inc in out:
            inc["alarms"] = [a for a in (hist.alarm(i) for i in inc["alert_ids"][:12]) if a]
        return out

    @app.post("/api/assistant/{alarm_id}")
    def explain(alarm_id: int, llm: bool = True, save: bool = True) -> dict:
        try:
            ctx = build_context(hist, alarm_id)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        ex = helper.explain(ctx, use_llm=llm)
        out = ex.to_dict()
        if save:
            out["note_id"] = hist.add_note(ctx.at_ts, alarm_id, ex.mode, ex.answer)
        return out

    @app.get("/api/notes")
    def notes() -> list[dict]:
        return hist.notes()

    @app.get("/api/kpis")
    def kpis() -> dict:
        end = hist.last_ts() or 0
        letter, start = reports.shift_of(end)
        d = reports.shift_data(hist, start)
        return {
            "shift": letter,
            "shift_start": start,
            "alarms": d["alarms_annunciated"],
            "alarms_per_hour": round(d["alarms_annunciated"] / max((end - start) / 3600, 1 / 60), 1),
            "suppressed": d["alarms_suppressed"],
            "acid_l": round(d["acid_l"], 1),
            "coagulant_l": round(d["coagulant_l"], 1),
            "energy_kwh": round(d["energy_kwh"], 1),
            "volume_m3": round(d["volume_m3"], 1),
        }

    @app.get("/reports/compliance", response_class=HTMLResponse)
    def compliance(date: str | None = None) -> str:
        if date:
            day = int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())
        else:
            last = hist.last_ts() or 0
            day = last - last % 86400
        return reports.render_compliance(reports.compliance_data(hist, day))

    @app.get("/reports/shift", response_class=HTMLResponse)
    def shift(which: str = "current") -> str:
        last = hist.last_ts() or 0
        _, start = reports.shift_of(last)
        if which == "previous":
            start -= 8 * 3600
        return reports.render_shift(reports.shift_data(hist, start))

    @app.post("/api/sim/inject")
    def inject(req: InjectRequest) -> dict:
        if live is None:
            raise HTTPException(409, "fault injection is only available in demo mode")
        try:
            f = live.inject(req.kind)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"injected": f.describe(), "duration_min": f.duration}

    return app
