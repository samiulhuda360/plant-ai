"""Builds the evidence for one alarm from the historian: the alarm, its incident companions and recent tag history.

Everything the assistant may state as a reading comes from here. ``facts_text`` is what the model sees, and
``numbers`` is the set the number guard checks the answer against.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..detect.rules import AREA_TAGS
from ..historian import Historian
from ..tags import AREAS, BY_NAME, TAG_NAMES

WINDOW_MIN = 60


def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M")


def fmt(v: float) -> str:
    if abs(v) >= 100:
        return f"{v:.0f}"
    if abs(v) >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


@dataclass
class Trend:
    tag: str
    description: str
    unit: str
    now: float
    lo: float
    hi: float
    change_30: float

    def line(self) -> str:
        u = f" {self.unit}" if self.unit else ""
        return (
            f"- {self.tag} ({self.description}): now {fmt(self.now)}{u}; last {WINDOW_MIN} min range "
            f"{fmt(self.lo)} to {fmt(self.hi)}{u}; change over 30 min {fmt(self.change_30)}{u}"
        )


@dataclass
class AlarmContext:
    alarm: dict
    related: list[dict]
    trends: list[Trend]
    at_ts: int
    extra: dict = field(default_factory=dict)

    def facts_text(self) -> str:
        a = self.alarm
        tag = BY_NAME.get(a["tag"])
        unit = f" {tag.unit}" if tag and tag.unit else ""
        lines = [
            f"Plant time now: {fmt_ts(self.at_ts)}",
            f"Selected alarm: [{a['priority']}] {a['message']} (rule {a['rule_id']}, tag {a['tag']}, "
            f"area {a['area']} {AREAS.get(a['area'], '')}), raised {fmt_ts(a['raised_ts'])}, "
            f"value at raise {fmt(a['value'])}{unit if a['layer'] == 'limit' else ''}, state {a['state']}",
        ]
        ev = a.get("evidence") or {}
        if ev:
            lines.append("Alarm evidence: " + "; ".join(f"{k} = {_ev(v)}" for k, v in ev.items()))
        if self.related:
            lines.append("Other alarms in the same incident or active at the same time:")
            for r in self.related[:8]:
                lines.append(f"- {fmt_ts(r['raised_ts'])} [{r['priority']}] {r['message']} ({r['tag']}, {r['state']})")
        lines.append(f"Recent tag history (last {WINDOW_MIN} minutes):")
        lines += [t.line() for t in self.trends]
        return "\n".join(lines)

    def query(self) -> str:
        a = self.alarm
        parts = [a["message"], a["tag"]]
        if a["tag"] in BY_NAME:
            parts.append(BY_NAME[a["tag"]].description)
        for k in (a.get("evidence") or {}).get("top_contributors", {}):
            parts += [k, BY_NAME[k].description if k in BY_NAME else ""]
        for r in self.related[:5]:
            parts += [r["message"], r["tag"]]
        return " ".join(parts)

    def numbers(self) -> set[float]:
        return numbers_in(self.facts_text())


def _ev(v: object) -> str:
    if isinstance(v, dict):
        return ", ".join(f"{k} {_ev(x)}" for k, x in v.items())
    if isinstance(v, float):
        return fmt(v)
    return str(v)


NUM_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
REF_RE = re.compile(r"\b(?:[A-Z]{1,3}-\d{3}[A-Z]?(?:_[A-Z]+)?|SOP-\d{2}(?:\s+\d+\.\d+)?)\b")


def numbers_in(text: str) -> set[float]:
    """Numbers in a text, ignoring instrument tags (AIT-301) and SOP references (SOP-06 6.4)."""
    text = REF_RE.sub(" ", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text)
    out: set[float] = set()
    for m in NUM_RE.findall(text.replace(":", " ")):
        try:
            out.add(float(m))
        except ValueError:
            continue
    return out


def build_context(hist: Historian, alarm_id: int, at_ts: int | None = None, max_trends: int = 10) -> AlarmContext:
    a = hist.alarm(alarm_id)
    if a is None:
        raise KeyError(f"alarm {alarm_id} not found")
    now = hist.last_ts() or a["raised_ts"]
    # An active alarm is explained with the latest data; a past one with the hour around it.
    at = at_ts or (now if a["cleared_ts"] is None else min(now, max(a["raised_ts"] + 15 * 60, a["cleared_ts"])))
    window = hist.alarms(a["raised_ts"] - 3600, at, limit=200)
    related = [
        r
        for r in window
        if r["id"] != a["id"]
        and r["suppressed"] is None
        and (r["incident_id"] == a["incident_id"] or r["cleared_ts"] is None or r["cleared_ts"] >= a["raised_ts"])
    ]
    related.sort(key=lambda r: r["raised_ts"])

    tags: list[str] = []

    def add(t: str) -> None:
        if t in BY_NAME and BY_NAME[t].kind != "counter" and t not in tags and t in TAG_NAMES:
            tags.append(t)

    add(a["tag"])
    ev = a.get("evidence") or {}
    for k in ev.get("top_contributors", {}):
        add(k)
    for k in ("command", "suspect"):
        if isinstance(ev.get(k), str):
            add(ev[k])
    for t in _COMPANIONS.get(a["tag"], []):
        add(t)
    for r in related[:6]:
        add(r["tag"])
    for t in AREA_TAGS.get(a["area"], []):
        add(t)
    tags = tags[:max_trends]

    times, cols = hist.frame(tags, at - WINDOW_MIN * 60, at)
    trends = []
    for t in tags:
        vals = [v for v in cols[t] if not math.isnan(v)]
        if not vals:
            continue
        idx30 = max(0, len(vals) - 31)
        tag = BY_NAME[t]
        trends.append(Trend(t, tag.description, tag.unit, vals[-1], min(vals), max(vals), vals[-1] - vals[idx30]))
    return AlarmContext(a, related, trends, at)


_COMPANIONS = {
    "B-301_RUN": ["B-301_CMD", "AIT-301", "JT-301", "SC-301"],
    "AIT-301": ["B-301_RUN", "JT-301", "SC-301"],
    "FIT-202": ["P-202_SP", "LIT-202", "AIT-401"],
    "ZT-101": ["FV-101", "LIT-101", "FIT-102"],
    "AIT-201": ["AIT-201B", "AIT-501", "P-201_SP"],
    "AIT-201B": ["AIT-201", "AIT-501", "P-201_SP"],
    "LIT-202": ["FIT-202", "P-202_SP"],
    "LIT-901": ["DS-900", "FIT-901"],
    "AIT-401": ["FIT-202", "P-202_SP", "FIT-102"],
}
