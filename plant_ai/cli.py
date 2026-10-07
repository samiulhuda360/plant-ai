"""Command line: run the demo, the individual services, reports and the evaluations."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
import time
import webbrowser
from pathlib import Path

from . import paths


def _db(args: argparse.Namespace) -> Path:
    return Path(args.db) if args.db else paths.HISTORIAN


# ---------------------------------------------------------------------- services
def cmd_demo(args: argparse.Namespace) -> None:
    import uvicorn

    from .live import LivePlant
    from .web.app import create_app

    db = _db(args)
    plant = LivePlant(db, seed=args.seed, port=args.port, tick_s=args.tick)
    print(f"Backfilling {args.hours} h of plant history into {db} ...", flush=True)
    t0 = time.time()
    plant.backfill(args.hours)
    speed = 60 / args.tick
    print(f"  done in {time.time() - t0:.1f} s; live simulation at {speed:.0f}x real time from now", flush=True)
    app = create_app(db, live=plant)
    config = uvicorn.Config(app, host=args.host, port=args.http, log_level="warning")
    server = uvicorn.Server(config)

    async def main() -> None:
        stop = asyncio.Event()
        plant_task = asyncio.create_task(plant.run(stop))
        print(f"Modbus TCP PLC simulator on 127.0.0.1:{args.port}; collector polling it into the historian")
        print(f"Dashboard: http://{args.host}:{args.http}  (Ctrl+C to stop)", flush=True)
        if not args.no_browser:
            webbrowser.open(f"http://{args.host}:{args.http}")
        try:
            await server.serve()
        finally:
            stop.set()
            await plant_task

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())


def cmd_simulate(args: argparse.Namespace) -> None:
    from .live import DEMO_HISTORY_FAULTS
    from .sim.faults import schedule

    db = _db(args)
    from .live import LivePlant

    plant = LivePlant(db, seed=args.seed, port=args.port)
    faults = {"demo": DEMO_HISTORY_FAULTS, "none": [], "random": schedule(args.seed, int(args.hours * 60))}[args.faults]
    plant.backfill(args.hours, faults)
    n = plant.hist.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    a = plant.hist.db.execute("SELECT COUNT(*) FROM alarms").fetchone()[0]
    print(f"{db}: {n} samples, {a} alarms over {args.hours} h; faults: {[f.describe() for f in faults]}")


def cmd_modbus_server(args: argparse.Namespace) -> None:
    from .protocols.modbus_server import PlantModbusServer
    from .sim.process import PlantSimulator

    srv = PlantModbusServer(PlantSimulator(seed=args.seed), "127.0.0.1", args.port, args.tick)
    print(f"Modbus TCP PLC simulator on 127.0.0.1:{args.port} (Ctrl+C to stop)")
    stop = asyncio.Event()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(srv.run(stop))


def cmd_collect(args: argparse.Namespace) -> None:
    from .detect.anomaly import default_model
    from .detect.engine import DetectionEngine
    from .historian import Historian
    from .live import resume_sequences
    from .protocols.collector import Collector

    hist = Historian(_db(args))
    engine = DetectionEngine(pca=default_model())
    resume_sequences(engine, hist)
    col = Collector(hist, engine, args.host, args.port, args.poll)
    print(f"Collecting from {args.host}:{args.port} into {_db(args)} (Ctrl+C to stop)")
    try:
        asyncio.run(col.run(asyncio.Event()))
    except KeyboardInterrupt:
        print(f"polls {col.polls}, stored {col.stored}, errors {col.errors}")


def cmd_dashboard(args: argparse.Namespace) -> None:
    import uvicorn

    from .web.app import create_app

    print(f"Dashboard on http://{args.host}:{args.http} over {_db(args)}")
    uvicorn.run(create_app(_db(args)), host=args.host, port=args.http, log_level="warning")


def cmd_read_tags(args: argparse.Namespace) -> None:
    from pymodbus.client import AsyncModbusTcpClient

    from .protocols.collector import decode_block
    from .tags import BY_NAME, IR_COUNT

    async def main() -> None:
        c = AsyncModbusTcpClient(args.host, port=args.port)
        await c.connect()
        rr = await c.read_input_registers(0, count=IR_COUNT, device_id=1)
        c.close()
        ts, values = decode_block(list(rr.registers))
        from .assistant.context import fmt_ts

        print(f"plant time {fmt_ts(ts)}  ({IR_COUNT} input registers read in one request)")
        for name, v in values.items():
            t = BY_NAME[name]
            print(f"  {name:10s} {v:10.2f} {t.unit:6s} {t.description}")

    asyncio.run(main())


def cmd_write_setpoint(args: argparse.Namespace) -> None:
    from pymodbus.client import AsyncModbusTcpClient

    from .tags import HR_ADDRESS, encode

    async def main() -> None:
        c = AsyncModbusTcpClient(args.host, port=args.port)
        await c.connect()
        r = await c.write_register(HR_ADDRESS[args.name], encode(args.name, args.value), device_id=1)
        c.close()
        print("refused (out of range)" if r.isError() else f"{args.name} <- {args.value}")

    asyncio.run(main())


# ---------------------------------------------------------------------- assistant and reports
def cmd_explain(args: argparse.Namespace) -> None:
    from .assistant.context import build_context
    from .assistant.explain import Assistant
    from .assistant.llm import LLM
    from .historian import Historian

    hist = Historian(_db(args))
    ex = Assistant(llm=LLM() if args.llm else None).explain(build_context(hist, args.alarm_id), use_llm=args.llm)
    print(json.dumps(ex.to_dict() if args.json else {"mode": ex.mode, **ex.answer}, indent=2))


def cmd_report(args: argparse.Namespace) -> None:
    from . import reports
    from .historian import Historian

    hist = Historian(_db(args))
    last = hist.last_ts() or 0
    if args.kind == "compliance":
        day = last - last % 86400 if not args.date else _date_ts(args.date)
        html_text = reports.render_compliance(reports.compliance_data(hist, day))
        name = f"compliance-{reports.compliance_data(hist, day)['date']}.html"
    else:
        _, start = reports.shift_of(last)
        if args.previous:
            start -= 8 * 3600
        d = reports.shift_data(hist, start)
        html_text = reports.render_shift(d)
        name = f"shift-{d['shift']}-{start}.html"
    out = Path(args.out) if args.out else paths.REPORTS / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    print(f"wrote {out}")


def _date_ts(s: str) -> int:
    from datetime import UTC, datetime

    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())


# ---------------------------------------------------------------------- evaluation
def cmd_eval(args: argparse.Namespace) -> None:
    from .eval import report

    report.run(args.which, use_llm=args.llm, offline=args.offline, quick=args.quick, embeddings=args.embeddings)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("pymodbus").setLevel(logging.ERROR)
    p = argparse.ArgumentParser(prog="plant-ai", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def db_arg(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--db", help=f"historian path (default {paths.HISTORIAN})")

    sp = sub.add_parser("demo", help="simulator + Modbus server + collector + dashboard, with backfilled history")
    db_arg(sp)
    sp.add_argument("--hours", type=float, default=26, help="hours of history to backfill first")
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--port", type=int, default=5020, help="Modbus TCP port")
    sp.add_argument("--http", type=int, default=8000)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--tick", type=float, default=1.0, help="real seconds per simulated minute")
    sp.add_argument("--no-browser", action="store_true")
    sp.set_defaults(func=cmd_demo)

    sp = sub.add_parser("simulate", help="write simulated history straight into the historian")
    db_arg(sp)
    sp.add_argument("--hours", type=float, default=48)
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--faults", choices=["demo", "none", "random"], default="demo")
    sp.add_argument("--port", type=int, default=5020)
    sp.set_defaults(func=cmd_simulate)

    sp = sub.add_parser("modbus-server", help="run only the PLC simulator (Modbus TCP server)")
    sp.add_argument("--port", type=int, default=5020)
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--tick", type=float, default=1.0)
    sp.set_defaults(func=cmd_modbus_server)

    sp = sub.add_parser("collect", help="run only the collector (Modbus TCP client -> historian + detection)")
    db_arg(sp)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=5020)
    sp.add_argument("--poll", type=float, default=0.25)
    sp.set_defaults(func=cmd_collect)

    sp = sub.add_parser("dashboard", help="serve the dashboard over an existing historian")
    db_arg(sp)
    sp.add_argument("--http", type=int, default=8000)
    sp.add_argument("--host", default="127.0.0.1")
    sp.set_defaults(func=cmd_dashboard)

    sp = sub.add_parser("read-tags", help="read every tag from the PLC simulator over Modbus TCP")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=5020)
    sp.set_defaults(func=cmd_read_tags)

    sp = sub.add_parser("write-setpoint", help="write a setpoint holding register over Modbus TCP")
    sp.add_argument("name", choices=["AIC-201_SP", "AIC-301_SP", "LIC-101_SP", "CTRL_MODE"])
    sp.add_argument("value", type=float)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=5020)
    sp.set_defaults(func=cmd_write_setpoint)

    sp = sub.add_parser("explain", help="ask the maintenance assistant about an alarm in the historian")
    db_arg(sp)
    sp.add_argument("alarm_id", type=int)
    sp.add_argument("--llm", action="store_true", help="use the model (needs AI_API_KEY)")
    sp.add_argument("--json", action="store_true", help="print the full result with guards and retrieval")
    sp.set_defaults(func=cmd_explain)

    sp = sub.add_parser("report", help="write the daily compliance report or the shift report as HTML")
    db_arg(sp)
    sp.add_argument("kind", choices=["compliance", "shift"])
    sp.add_argument("--date", help="YYYY-MM-DD (compliance; default: the latest day)")
    sp.add_argument("--previous", action="store_true", help="the previous shift instead of the current one")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("eval", help="run the evaluations and write eval/results")
    sp.add_argument("which", choices=["detection", "dosing", "assistant", "all"])
    sp.add_argument("--llm", action="store_true", help="assistant: also run the LLM + guards mode")
    sp.add_argument("--offline", action="store_true", help="assistant: use only cached model answers")
    sp.add_argument("--embeddings", action="store_true", help="assistant: also score BM25 + embeddings retrieval")
    sp.add_argument("--quick", action="store_true", help="detection: 3 held-out seeds only (for CI)")
    sp.set_defaults(func=cmd_eval)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
