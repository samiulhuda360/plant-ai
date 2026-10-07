"""Daily discharge-compliance report and shift report, generated from the historian.

Both are built as plain data first (so tests and the API can use them) and then rendered as a self-contained,
printable HTML page.
"""

from __future__ import annotations

import html
import math
from collections import Counter
from datetime import UTC, datetime

from .assistant.context import fmt_ts
from .detect.rules import PRIORITIES
from .historian import Historian
from .tags import BY_NAME, CONSENT

PLANT = "Acme Textiles - effluent treatment plant"
SHIFTS = {"A": 6, "B": 14, "C": 22}  # shift start hours, plant time


def _clean(values: list[float]) -> list[float]:
    return [v for v in values if not math.isnan(v)]


def _integral_per_hour(values: list[float]) -> float:
    """Sum of a per-hour rate sampled every minute (L/h, m3/h or kW) -> L, m3 or kWh."""
    return sum(_clean(values)) / 60.0


def _events(times: list[int], ok: list[bool], values: list[float], hi: bool) -> list[dict]:
    events, start, peak = [], None, None
    for t, good, v in zip([*times, None], [*ok, True], [*values, 0.0], strict=True):
        if not good and start is None:
            start, peak = t, v
        elif not good:
            peak = max(peak, v) if hi else min(peak, v)
        elif start is not None:
            end = t if t is not None else times[-1] + 60
            events.append({"start": start, "end": end, "minutes": (end - start) // 60, "peak": peak})
            start = None
    return events


def compliance_data(hist: Historian, day_start: int) -> dict:
    end = day_start + 86400 - 60
    tags = [*CONSENT, "FIT-501", "FIT-202", "P-201_SP", "JT-001", "JT-301"]
    times, cols = hist.frame(tags, day_start, end)
    n = len(times)
    params = []
    all_ok = [True] * n
    for tag, (lo, hi) in CONSENT.items():
        vals = cols[tag]
        ok = [math.isnan(v) or ((lo is None or v >= lo) and (hi is None or v <= hi)) for v in vals]
        all_ok = [a and b for a, b in zip(all_ok, ok, strict=True)]
        clean = _clean(vals)
        is_hi = hi is not None and (lo is None or (clean and max(clean) > hi))
        params.append(
            {
                "tag": tag,
                "name": BY_NAME[tag].description,
                "unit": BY_NAME[tag].unit,
                "limit": (f"{lo} to {hi}" if lo is not None else f"<= {hi}"),
                "min": min(clean) if clean else None,
                "mean": sum(clean) / len(clean) if clean else None,
                "max": max(clean) if clean else None,
                "minutes_out": sum(not o for o in ok),
                "compliant_pct": 100.0 * sum(ok) / n if n else None,
                "events": _events(times, ok, vals, bool(is_hi)),
            }
        )
    return {
        "plant": PLANT,
        "date": datetime.fromtimestamp(day_start, UTC).strftime("%Y-%m-%d"),
        "start": day_start,
        "end": end,
        "samples": n,
        "completeness_pct": 100.0 * n / 1440,
        "compliant_pct": 100.0 * sum(all_ok) / n if n else None,
        "volume_m3": _integral_per_hour(cols["FIT-501"]),
        "acid_l": _integral_per_hour(cols["P-201_SP"]),
        "coagulant_l": _integral_per_hour(cols["FIT-202"]),
        "energy_kwh": _integral_per_hour(cols["JT-001"]),
        "blower_kwh": _integral_per_hour(cols["JT-301"]),
        "params": params,
        "incidents": hist.incidents(limit=100, start=day_start, end=end),
    }


def shift_of(ts: int) -> tuple[str, int]:
    """The shift letter and the shift start for a plant timestamp."""
    hour = (ts % 86400) // 3600
    day0 = ts - ts % 86400
    if 6 <= hour < 14:
        return "A", day0 + 6 * 3600
    if 14 <= hour < 22:
        return "B", day0 + 14 * 3600
    start = day0 + 22 * 3600 if hour >= 22 else day0 - 2 * 3600
    return "C", start


def shift_data(hist: Historian, shift_start: int) -> dict:
    end = shift_start + 8 * 3600 - 60
    letter, _ = shift_of(shift_start)
    alarms = [a for a in hist.alarms(shift_start, end, limit=5000)]
    shown = [a for a in alarms if a["suppressed"] is None]
    by_prio = Counter(a["priority"] for a in shown)
    times = sorted(a["raised_ts"] for a in shown)
    peak, j = 0, 0
    for i, t in enumerate(times):
        while times[j] <= t - 600:
            j += 1
        peak = max(peak, i - j + 1)
    standing = [a for a in hist.active_alarms() if a["cleared_ts"] is None and a["raised_ts"] <= end]
    tanks = ["LIT-201", "LIT-202", "LIT-901", "LIT-902", "LIT-903"]
    ttimes, tcols = hist.frame([*tanks, "FIT-501", "FIT-202", "P-201_SP", "JT-001"], shift_start, end)
    levels = []
    for t in tanks:
        vals = _clean(tcols[t])
        if vals:
            levels.append(
                {"tag": t, "name": BY_NAME[t].description, "start": vals[0], "end": vals[-1], "min": min(vals)}
            )
    comp = compliance_window(hist, shift_start, end)
    notes = [n for n in hist.notes(limit=200) if shift_start <= n["ts"] <= end]
    return {
        "plant": PLANT,
        "shift": letter,
        "start": shift_start,
        "end": end,
        "samples": len(ttimes),
        "alarms_annunciated": len(shown),
        "alarms_suppressed": len(alarms) - len(shown),
        "alarms_per_hour": len(shown) / 8.0,
        "peak_10min": peak,
        "by_priority": {p: by_prio.get(p, 0) for p in reversed(PRIORITIES)},
        "bad_actors": Counter(f"{a['rule_id']}: {a['message']}" for a in shown).most_common(5),
        "standing": standing,
        "incidents": hist.incidents(limit=100, start=shift_start, end=end),
        "volume_m3": _integral_per_hour(tcols["FIT-501"]),
        "acid_l": _integral_per_hour(tcols["P-201_SP"]),
        "coagulant_l": _integral_per_hour(tcols["FIT-202"]),
        "energy_kwh": _integral_per_hour(tcols["JT-001"]),
        "levels": levels,
        "compliance": comp,
        "notes": notes,
    }


def compliance_window(hist: Historian, start: int, end: int) -> dict:
    times, cols = hist.frame(list(CONSENT), start, end)
    out = {}
    for tag, (lo, hi) in CONSENT.items():
        out[tag] = sum(
            1 for v in cols[tag] if not math.isnan(v) and not ((lo is None or v >= lo) and (hi is None or v <= hi))
        )
    return out


# ---------------------------------------------------------------------- HTML
CSS = """
:root { --bg:#f4f5f4; --panel:#ffffff; --ink:#1d2a28; --muted:#5d6b68; --line:#d5dbd9; --accent:#2f6f68;
        --ok:#2e7d4f; --warn:#b7791f; --bad:#b3261e; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 "Segoe UI", system-ui, sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 24px 16px 48px; }
header { border-bottom: 3px solid var(--accent); margin-bottom: 18px; padding-bottom: 10px; }
h1 { font-size: 22px; margin: 0; } h2 { font-size: 16px; margin: 22px 0 8px; color: var(--accent); }
.sub { color: var(--muted); }
.tiles { display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.tile { background: var(--panel); border:1px solid var(--line); border-radius:6px; padding:10px 12px; }
.tile b { display:block; font-size: 20px; } .tile span { color: var(--muted); font-size: 12px; }
table { width:100%; border-collapse: collapse; background: var(--panel); border:1px solid var(--line); }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); font-size: 13px; vertical-align: top; }
th { background:#e9eeec; font-weight:600; }
.ok { color: var(--ok); font-weight:600; } .bad { color: var(--bad); font-weight:600; } .warn { color: var(--warn); font-weight:600; }
.sign { display:grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 28px; }
.sign div { border-top: 1px solid var(--ink); padding-top: 4px; color: var(--muted); }
@media print { body { background:#fff; } }
"""


def _f(v: float | None, digits: int = 1) -> str:
    return "-" if v is None else f"{v:,.{digits}f}"


def _page(title: str, body: str) -> str:
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>"


def render_compliance(d: dict) -> str:
    e = html.escape
    ok_all = d["compliant_pct"] is not None and d["compliant_pct"] >= 99.999
    rows = ""
    for p in d["params"]:
        cls = "ok" if p["minutes_out"] == 0 else "bad"
        rows += (
            f"<tr><td>{e(p['tag'])}</td><td>{e(p['name'])}</td><td>{e(p['limit'])} {e(p['unit'])}</td>"
            f"<td>{_f(p['min'], 2)}</td><td>{_f(p['mean'], 2)}</td><td>{_f(p['max'], 2)}</td>"
            f"<td class='{cls}'>{p['minutes_out']}</td><td class='{cls}'>{_f(p['compliant_pct'], 2)} %</td></tr>"
        )
    ev_rows = ""
    for p in d["params"]:
        for ev in p["events"]:
            ev_rows += (
                f"<tr><td>{e(p['tag'])} {e(p['name'])}</td><td>{fmt_ts(ev['start'])}</td><td>{fmt_ts(ev['end'])}</td>"
                f"<td>{ev['minutes']}</td><td>{_f(ev['peak'], 2)} {e(p['unit'])}</td></tr>"
            )
    ev_html = (
        f"<table><tr><th>Parameter</th><th>From</th><th>To</th><th>Minutes</th><th>Peak</th></tr>{ev_rows}</table>"
        if ev_rows
        else "<p class='ok'>No exceedances of the discharge consent.</p>"
    )
    inc = "".join(
        f"<tr><td>#{i['id']}</td><td>{fmt_ts(i['opened_ts'])}</td><td>{e(i['severity'])}</td><td>{e(i['title'])}</td>"
        f"<td>{len(i['alert_ids'])}</td></tr>"
        for i in d["incidents"]
    )
    inc_html = (
        f"<table><tr><th>Incident</th><th>Opened</th><th>Severity</th><th>First-out alarm</th><th>Alarms</th></tr>{inc}</table>"
        if inc
        else "<p>No incidents.</p>"
    )
    body = f"""
<header><h1>Daily discharge compliance report</h1>
<div class='sub'>{e(d["plant"])} &middot; {d["date"]} (plant time, 00:00 to 24:00) &middot; generated from the historian</div></header>
<div class='tiles'>
<div class='tile'><span>Overall compliance</span><b class='{"ok" if ok_all else "bad"}'>{_f(d["compliant_pct"], 2)} %</b></div>
<div class='tile'><span>Treated volume</span><b>{_f(d["volume_m3"], 0)} m3</b></div>
<div class='tile'><span>Sulphuric acid (30 %)</span><b>{_f(d["acid_l"], 0)} L</b></div>
<div class='tile'><span>PAC coagulant</span><b>{_f(d["coagulant_l"], 0)} L</b></div>
<div class='tile'><span>Plant energy</span><b>{_f(d["energy_kwh"], 0)} kWh</b></div>
<div class='tile'><span>Data completeness</span><b>{_f(d["completeness_pct"], 1)} %</b></div>
</div>
<h2>Discharge parameters against consent</h2>
<table><tr><th>Tag</th><th>Parameter</th><th>Consent</th><th>Min</th><th>Mean</th><th>Max</th><th>Minutes out</th><th>Compliant</th></tr>{rows}</table>
<h2>Exceedance events</h2>{ev_html}
<h2>Incidents</h2>{inc_html}
<h2>Comments</h2><p class='sub'>Cause and corrective actions for any exceedance (SOP-10), to be completed by the shift supervisor.</p>
<div class='sign'><div>Prepared by (operator)</div><div>Reviewed by (environmental officer)</div></div>"""
    return _page(f"Compliance report {d['date']}", body)


def render_shift(d: dict) -> str:
    e = html.escape
    prio = "".join(f"<div class='tile'><span>{p}</span><b>{n}</b></div>" for p, n in d["by_priority"].items())
    bad = (
        "".join(f"<tr><td>{e(k)}</td><td>{n}</td></tr>" for k, n in d["bad_actors"])
        or "<tr><td colspan=2>None</td></tr>"
    )
    standing = (
        "".join(
            f"<tr><td>{fmt_ts(a['raised_ts'])}</td><td>{e(a['priority'])}</td><td>{e(a['message'])}</td><td>{e(a['state'])}</td></tr>"
            for a in d["standing"]
        )
        or "<tr><td colspan=4>No standing alarms.</td></tr>"
    )
    inc = (
        "".join(
            f"<tr><td>#{i['id']}</td><td>{fmt_ts(i['opened_ts'])}</td><td>{e(i['severity'])}</td><td>{e(i['title'])}</td><td>{e(i['status'])}</td></tr>"
            for i in d["incidents"]
        )
        or "<tr><td colspan=5>No incidents.</td></tr>"
    )
    lv = "".join(
        f"<tr><td>{e(t['tag'])}</td><td>{e(t['name'])}</td><td>{_f(t['start'])} %</td><td>{_f(t['end'])} %</td>"
        f"<td class='{'bad' if t['min'] < 20 else 'ok'}'>{_f(t['min'])} %</td></tr>"
        for t in d["levels"]
    )
    comp = "".join(
        f"<tr><td>{e(k)} {e(BY_NAME[k].description)}</td><td class='{'ok' if v == 0 else 'bad'}'>{v}</td></tr>"
        for k, v in d["compliance"].items()
    )
    notes = (
        "".join(
            f"<tr><td>{fmt_ts(n['ts'])}</td><td>{e(n['mode'])}</td><td>{e(n['body'].get('handover', ''))}</td></tr>"
            for n in d["notes"]
        )
        or "<tr><td colspan=3>No assistant notes saved this shift.</td></tr>"
    )
    rate_cls = "ok" if d["alarms_per_hour"] <= 6 else "warn"
    body = f"""
<header><h1>Shift report - shift {d["shift"]}</h1>
<div class='sub'>{e(d["plant"])} &middot; {fmt_ts(d["start"])} to {fmt_ts(d["end"] + 60)} (plant time)</div></header>
<div class='tiles'>
<div class='tile'><span>Alarms annunciated</span><b>{d["alarms_annunciated"]}</b></div>
<div class='tile'><span>Alarms per hour (ISA-18.2 target 6 or fewer)</span><b class='{rate_cls}'>{_f(d["alarms_per_hour"], 1)}</b></div>
<div class='tile'><span>Peak alarms in 10 min</span><b>{d["peak_10min"]}</b></div>
<div class='tile'><span>Suppressed (logged only)</span><b>{d["alarms_suppressed"]}</b></div>
<div class='tile'><span>Treated volume</span><b>{_f(d["volume_m3"], 0)} m3</b></div>
<div class='tile'><span>Acid / PAC used</span><b>{_f(d["acid_l"], 0)} / {_f(d["coagulant_l"], 0)} L</b></div>
<div class='tile'><span>Energy</span><b>{_f(d["energy_kwh"], 0)} kWh</b></div>
</div>
<h2>Alarms by priority</h2><div class='tiles'>{prio}</div>
<h2>Most frequent alarms (bad actors)</h2><table><tr><th>Alarm</th><th>Count</th></tr>{bad}</table>
<h2>Incidents</h2><table><tr><th>Incident</th><th>Opened</th><th>Severity</th><th>First-out alarm</th><th>Status</th></tr>{inc}</table>
<h2>Standing alarms at the end of the shift</h2><table><tr><th>Raised</th><th>Priority</th><th>Alarm</th><th>State</th></tr>{standing}</table>
<h2>Chemical tank levels</h2><table><tr><th>Tag</th><th>Tank</th><th>Shift start</th><th>Shift end</th><th>Lowest</th></tr>{lv}</table>
<h2>Minutes out of discharge consent</h2><table><tr><th>Parameter</th><th>Minutes</th></tr>{comp}</table>
<h2>Handover notes drafted by the assistant</h2><table><tr><th>Time</th><th>Mode</th><th>Note</th></tr>{notes}</table>
<div class='sign'><div>Outgoing operator</div><div>Incoming operator</div></div>"""
    return _page(f"Shift report {d['shift']} {fmt_ts(d['start'])}", body)
