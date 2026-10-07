"""Modbus TCP server that exposes the running simulator like a PLC would.

* Input registers (function code 4): 0-1 plant time in minutes since the Unix epoch (high word first), then one
  register per measured tag in ``tags.TAGS`` order, unsigned and scaled by the tag's ``scale``.
* Holding registers (function codes 3, 6, 16): the setpoints in ``tags.SETPOINTS`` order. Writes outside a
  setpoint's engineering range are refused with ILLEGAL_DATA_VALUE.

The register image is refreshed on every read through pymodbus's device ``action`` hook, so a client always reads
the latest simulator minute.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from pymodbus.constants import ExcCodes
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from ..sim.process import PlantSimulator
from ..tags import BY_NAME, HR_COUNT, IR_COUNT, IR_FIRST_TAG, SETPOINTS, TAGS, decode, encode

log = logging.getLogger(__name__)
DEVICE_ID = 1


def input_image(sim: PlantSimulator, values: dict[str, float]) -> list[int]:
    minutes = sim.ts // 60
    regs = [(minutes >> 16) & 0xFFFF, minutes & 0xFFFF]
    regs += [encode(t.name, values[t.name]) for t in TAGS]
    return regs


def holding_image(sim: PlantSimulator) -> list[int]:
    sp = sim.setpoints()
    return [encode(t.name, sp[t.name]) for t in SETPOINTS]


class PlantModbusServer:
    def __init__(self, sim: PlantSimulator, host: str = "127.0.0.1", port: int = 5020, tick_s: float = 0.5):
        self.sim = sim
        self.host, self.port, self.tick_s = host, port, tick_s
        self.values: dict[str, float] = sim.step()
        self.ir = input_image(sim, self.values)
        self.paused = False
        self.writes: list[tuple[str, float]] = []
        self.device = SimDevice(
            id=DEVICE_ID,
            simdata=(
                [SimData(0, count=1, values=False, datatype=DataType.BITS)],
                [SimData(0, count=1, values=False, datatype=DataType.BITS)],
                [SimData(0, count=HR_COUNT, values=0, datatype=DataType.REGISTERS)],
                [SimData(0, count=IR_COUNT, values=0, datatype=DataType.REGISTERS)],
            ),
            action=self._action,
        )

    async def _action(
        self,
        function_code: int,
        start_address: int,
        address: int,
        count: int,
        registers: list[int],
        set_values: list[int] | list[bool] | None,
    ) -> ExcCodes | None:
        if function_code == 4:
            registers[: len(self.ir)] = self.ir
        elif function_code == 3:
            registers[:HR_COUNT] = holding_image(self.sim)
        elif function_code in (6, 16) and set_values is not None:
            for i, raw in enumerate(set_values):
                name = SETPOINTS[address - start_address + i].name
                value = decode(name, int(raw))
                tag = BY_NAME[name]
                if not tag.lo <= value <= tag.hi:
                    return ExcCodes.ILLEGAL_VALUE
                self.sim.apply_setpoint(name, value)
                self.writes.append((name, value))
                log.info("setpoint %s <- %s", name, value)
        return None

    def tick(self) -> dict[str, float]:
        self.values = self.sim.step()
        self.ir = input_image(self.sim, self.values)
        return self.values

    async def run(self, stop: asyncio.Event) -> None:
        server = ModbusTcpServer(self.device, address=(self.host, self.port))  # needs the running loop
        await server.serve_forever(background=True)
        log.info("Modbus TCP server on %s:%s (%d input registers)", self.host, self.port, IR_COUNT)
        try:
            while not stop.is_set():
                if not self.paused:
                    self.tick()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.tick_s)
        finally:
            await server.shutdown()


assert IR_FIRST_TAG == 2
