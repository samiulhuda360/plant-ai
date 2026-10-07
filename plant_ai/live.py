"""The live plant for the demo: simulator, Modbus TCP server, collector and detection in one asyncio loop.

The simulator and the collector still talk over a real TCP socket (Modbus on localhost), exactly as they would if
the PLC and the server were separate machines. ``backfill`` first fills the historian with some hours of history
from the same simulator so the dashboard opens with trends, alarms and incidents to look at.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import numpy as np

from .detect.anomaly import default_model
from .detect.engine import DetectionEngine
from .historian import Historian
from .protocols.collector import Collector, persist
from .protocols.modbus_server import PlantModbusServer
from .sim.faults import FAULT_KINDS, Fault, random_fault
from .sim.process import PlantSimulator

log = logging.getLogger(__name__)

# Faults planted in the backfilled history (minutes from the start of the backfill).
DEMO_HISTORY_FAULTS = [
    Fault("pump_clog", 5 * 60, 150, "P-202", 0.8),
    Fault("shock_load", 11 * 60, 40, "FIT-101", 1.0, grace=180),
    Fault("flatline", 17 * 60, 150, "AIT-401"),
    Fault("blower_fail", 21 * 60 + 30, 70, "B-301"),
]


def resume_sequences(engine: DetectionEngine, hist: Historian) -> None:
    """Continue alarm and incident numbering after what the historian already holds."""
    engine._seq = hist.db.execute("SELECT COALESCE(MAX(id), 0) FROM alarms").fetchone()[0]
    engine._inc_seq = hist.db.execute("SELECT COALESCE(MAX(id), 0) FROM incidents").fetchone()[0]


class LivePlant:
    def __init__(
        self,
        db_path: Path,
        seed: int = 7,
        host: str = "127.0.0.1",
        port: int = 5020,
        tick_s: float = 1.0,
        fresh: bool = True,
    ) -> None:
        if fresh:
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(db_path) + suffix)
                if p.exists():
                    p.unlink()
        self.hist = Historian(db_path)
        self.sim = PlantSimulator(seed=seed)
        self.engine = DetectionEngine(pca=default_model())
        resume_sequences(self.engine, self.hist)
        self.rng = np.random.default_rng(seed + 1)
        self.server = PlantModbusServer(self.sim, host, port, tick_s)
        self.collector = Collector(Historian(db_path), self.engine, host, port, poll_s=min(0.25, tick_s / 3))
        self.host, self.port = host, port

    def backfill(self, hours: float, faults: list[Fault] | None = None) -> int:
        """Runs the simulator offline for some hours, storing samples and alarms like the collector would."""
        minutes = int(hours * 60)
        for f in faults if faults is not None else DEMO_HISTORY_FAULTS:
            if f.start < minutes:
                self.sim.faults.append(Fault(f.kind, self.sim.t + f.start, f.duration, f.target, f.magnitude, f.grace))
        for _ in range(minutes):
            ts = self.sim.ts
            values = self.sim.step()
            self.hist.write(ts, values, commit=False)
            persist(self.hist, self.engine.step(ts, values))
        self.hist.commit()
        self.server.tick()  # first live minute, served over Modbus
        self.collector.last_ts = self.hist.last_ts() or 0
        return minutes

    def inject(self, kind: str) -> Fault:
        if kind not in FAULT_KINDS:
            raise ValueError(f"unknown fault kind {kind}")
        fault = random_fault(kind, self.sim.t, self.rng)
        self.sim.inject(fault)
        log.info("training fault injected: %s", fault.describe())
        return fault

    def active_faults(self) -> list[dict]:
        return [
            {"kind": f.kind, "description": f.describe(), "minutes_left": f.end - self.sim.t}
            for f in self.sim.faults
            if f.active(self.sim.t)
        ]

    async def run(self, stop: asyncio.Event) -> None:
        server_task = asyncio.create_task(self.server.run(stop))
        await asyncio.sleep(0.5)
        collector_task = asyncio.create_task(self.collector.run(stop))
        await asyncio.gather(server_task, collector_task)
