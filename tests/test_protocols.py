"""Integration: the PLC simulator served over Modbus TCP, polled by the collector into the historian."""

from __future__ import annotations

import asyncio
import socket

from pymodbus.client import AsyncModbusTcpClient

from plant_ai.detect.engine import DetectionEngine
from plant_ai.detect.rules import THRESHOLD_ONLY
from plant_ai.historian import Historian
from plant_ai.protocols.collector import Collector, decode_block
from plant_ai.protocols.modbus_server import PlantModbusServer, input_image
from plant_ai.sim.process import PlantSimulator
from plant_ai.tags import HR_ADDRESS, IR_COUNT, TAG_NAMES, decode, encode


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_register_encoding_round_trip():
    sim = PlantSimulator(seed=1)
    values = sim.step()
    regs = input_image(sim, values)
    assert len(regs) == IR_COUNT
    ts, decoded = decode_block(regs)
    assert ts == sim.ts - sim.ts % 60
    for name in TAG_NAMES:
        assert abs(decoded[name] - min(values[name], 65535 / 10)) <= 1.0 or abs(decoded[name] - values[name]) < 0.051
    assert decode("AIT-201", encode("AIT-201", 7.31)) == 7.31


def test_collector_polls_the_server_into_the_historian(tmp_path):
    port = free_port()

    async def scenario() -> tuple[Collector, PlantModbusServer, list]:
        stop = asyncio.Event()
        server = PlantModbusServer(PlantSimulator(seed=2), port=port, tick_s=0.05)
        hist = Historian(tmp_path / "h.db")
        collector = Collector(hist, DetectionEngine(THRESHOLD_ONLY), port=port, poll_s=0.02)
        tasks = [asyncio.create_task(server.run(stop))]
        await asyncio.sleep(0.3)
        tasks.append(asyncio.create_task(collector.run(stop)))
        await asyncio.sleep(1.5)
        client = AsyncModbusTcpClient("127.0.0.1", port=port)
        await client.connect()
        ok = await client.write_register(HR_ADDRESS["AIC-301_SP"], encode("AIC-301_SP", 2.5), device_id=1)
        bad = await client.write_register(HR_ADDRESS["AIC-301_SP"], encode("AIC-301_SP", 9.0), device_id=1)
        hr = await client.read_holding_registers(0, count=4, device_id=1)
        client.close()
        stop.set()
        await asyncio.gather(*tasks)
        return collector, server, [ok.isError(), bad.isError(), list(hr.registers)]

    collector, server, (ok_err, bad_err, hr) = asyncio.run(scenario())
    assert collector.stored >= 10 and collector.errors == 0
    hist = Historian(tmp_path / "h.db")
    assert hist.meta("collector") == "connected"
    assert len(hist.series("AIT-301", 0, 2**40)) == collector.stored
    assert not ok_err and bad_err  # out-of-range setpoints are refused by the server
    assert server.sim.ctrl.do_sp == 2.5
    assert decode("AIC-301_SP", hr[HR_ADDRESS["AIC-301_SP"]]) == 2.5
