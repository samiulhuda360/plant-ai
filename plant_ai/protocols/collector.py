"""The collector service: polls the PLC's tags over Modbus TCP into the historian and runs detection online.

This is the "gather machine data on a server" piece: one block read of all input registers per poll, decode with
each tag's scale, store when the plant clock has moved on, then feed the same detection engine the evaluation
uses. Connection loss is recorded in the historian's status and retried with back-off.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from pymodbus.client import AsyncModbusTcpClient

from ..detect.engine import DetectionEngine, StepResult
from ..historian import Historian
from ..tags import IR_COUNT, IR_FIRST_TAG, TAG_NAMES, decode

log = logging.getLogger(__name__)
DEVICE_ID = 1


def decode_block(registers: list[int]) -> tuple[int, dict[str, float]]:
    minutes = (registers[0] << 16) | registers[1]
    values = {name: decode(name, registers[IR_FIRST_TAG + i]) for i, name in enumerate(TAG_NAMES)}
    return minutes * 60, values


def persist(hist: Historian, result: StepResult) -> None:
    for a in result.raised + result.cleared:
        hist.upsert_alarm(a.to_dict(), commit=False)
    for inc in result.incidents_opened + result.incidents_changed:
        hist.upsert_incident(inc.to_dict(), commit=False)


class Collector:
    def __init__(
        self,
        hist: Historian,
        engine: DetectionEngine,
        host: str = "127.0.0.1",
        port: int = 5020,
        poll_s: float = 0.25,
    ) -> None:
        self.hist, self.engine = hist, engine
        self.host, self.port, self.poll_s = host, port, poll_s
        self.last_ts = hist.last_ts() or 0
        self.polls = 0
        self.stored = 0
        self.errors = 0
        self.connected = False
        self.last_poll_wall = 0.0

    async def poll_once(self, client: AsyncModbusTcpClient) -> bool:
        rr = await client.read_input_registers(0, count=IR_COUNT, device_id=DEVICE_ID)
        if rr.isError():
            raise ConnectionError(str(rr))
        self.polls += 1
        self.last_poll_wall = time.time()
        ts, values = decode_block(list(rr.registers))
        if ts <= self.last_ts:
            return False
        self.hist.write(ts, values, commit=False)
        persist(self.hist, self.engine.step(ts, values))
        self.hist.commit()
        self.last_ts = ts
        self.stored += 1
        return True

    async def run(self, stop: asyncio.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            client = AsyncModbusTcpClient(self.host, port=self.port, timeout=2, retries=1)
            try:
                await client.connect()
                if not client.connected:
                    raise ConnectionError("connect failed")
                self.connected = True
                self.hist.set_meta("collector", "connected")
                backoff = 1.0
                log.info("collector connected to %s:%s", self.host, self.port)
                while not stop.is_set():
                    await self.poll_once(client)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=self.poll_s)
            except Exception as e:  # noqa: BLE001 - any comms failure: record, back off, reconnect
                self.errors += 1
                self.connected = False
                self.hist.set_meta("collector", f"disconnected: {e}")
                log.warning("collector: %s; retrying in %.0f s", e, backoff)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                client.close()
