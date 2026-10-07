from __future__ import annotations

import pytest

from plant_ai.live import LivePlant
from plant_ai.sim.faults import Fault


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory):
    """A historian with 10 hours of simulated history containing a blower trip and a pump clog."""
    db = tmp_path_factory.mktemp("hist") / "historian.db"
    plant = LivePlant(db, seed=11)
    plant.backfill(
        10,
        [Fault("blower_fail", 120, 60, "B-301"), Fault("pump_clog", 360, 150, "P-202", 0.85)],
    )
    plant.hist.close()
    plant.collector.hist.close()
    return db


@pytest.fixture()
def base_values():
    from plant_ai.sim.process import PlantSimulator

    return PlantSimulator(seed=3).step()
