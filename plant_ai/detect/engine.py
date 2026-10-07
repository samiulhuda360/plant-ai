"""The streaming detection engine: alarms, sensor health, PCA anomalies, flood suppression and incidents.

It is fed one sample per minute (a dict of tag values), in the evaluation straight from the simulator and in the
live system from the Modbus collector, so both paths run exactly the same code.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from ..tags import BY_NAME, TAG_NAMES
from .anomaly import EWMA, PCA_TAGS, PCAModel
from .rules import ALL_RULES, FLATLINE_TAGS, FULL, PCA_FACTOR, PRIORITY_RANK, SUPPRESSED_BY, Rule

HISTORY = 120  # minutes kept in memory for windowed checks


@dataclass
class Alert:
    id: int
    rule_id: str
    tag: str
    kind: str
    layer: str
    priority: str
    message: str
    raised_ts: int
    value: float
    area: str
    cleared_ts: int | None = None
    acked_ts: int | None = None
    suppressed: str | None = None  # None (annunciated) | "design" | "flood" | "shelved"
    incident_id: int | None = None
    evidence: dict = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.cleared_ts is None

    @property
    def state(self) -> str:
        """ISA-18.2 alarm state."""
        if self.active:
            return "ACTIVE_ACK" if self.acked_ts else "ACTIVE_UNACK"
        return "CLEARED" if self.acked_ts else "RTN_UNACK"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "tag": self.tag,
            "kind": self.kind,
            "layer": self.layer,
            "priority": self.priority,
            "message": self.message,
            "raised_ts": self.raised_ts,
            "cleared_ts": self.cleared_ts,
            "acked_ts": self.acked_ts,
            "value": self.value,
            "area": self.area,
            "suppressed": self.suppressed,
            "incident_id": self.incident_id,
            "state": self.state,
            "evidence": self.evidence,
        }


@dataclass
class Incident:
    id: int
    opened_ts: int
    title: str
    area: str
    severity: str
    alert_ids: list[int] = field(default_factory=list)
    closed_ts: int | None = None
    last_activity: int = 0
    last_raise: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "opened_ts": self.opened_ts,
            "closed_ts": self.closed_ts,
            "title": self.title,
            "area": self.area,
            "severity": self.severity,
            "alert_ids": list(self.alert_ids),
            "status": "open" if self.closed_ts is None else "closed",
        }


@dataclass
class StepResult:
    raised: list[Alert]
    cleared: list[Alert]
    incidents_opened: list[Incident]
    incidents_changed: list[Incident]


def _area(tag: str) -> str:
    if tag in BY_NAME:
        return BY_NAME[tag].area
    return "000"


class DetectionEngine:
    def __init__(
        self,
        layers: frozenset[str] = FULL,
        pca: PCAModel | None = None,
        flood_limit: int = 10,
        flood_window: int = 10,
        incident_quiet: int = 30,
        incident_group: int = 45,
        pca_factor: float = PCA_FACTOR,
        suppression: bool = True,
    ) -> None:
        self.layers = layers
        self.rules: list[Rule] = [r for r in ALL_RULES if r.layer in layers]
        if "anomaly" in layers and pca is None:
            self.rules = [r for r in self.rules if r.layer != "anomaly"]
        self.pca = pca
        self.pca_factor = pca_factor
        self.flood_limit = flood_limit
        self.flood_window = flood_window
        self.incident_quiet = incident_quiet
        self.incident_group = incident_group
        self.by_id: dict[int, Alert] = {}
        self.suppression = suppression
        self.idx = {n: i for i, n in enumerate(TAG_NAMES)}
        self.buf = np.zeros((HISTORY, len(TAG_NAMES)))
        self.n = 0  # samples seen
        self.pending: dict[str, int] = {}
        self.clearing: dict[str, int] = {}
        self.active: dict[str, Alert] = {}
        self.alerts: list[Alert] = []
        self.incidents: list[Incident] = []
        self.shelved: dict[str, int] = {}  # rule id -> shelved until ts
        self.annunciated_times: deque[int] = deque()
        self._pca_idx = [self.idx[t] for t in (pca.tags if pca else PCA_TAGS)]
        self._smooth = EWMA()
        self.last_pca: tuple[float, float] = (0.0, 0.0)
        self._last_x = np.zeros(0)
        self.anchor: dict[str, float] = {}
        self.unchanged: dict[str, int] = dict.fromkeys(FLATLINE_TAGS, 0)
        self._seq = 0
        self._inc_seq = 0

    # ------------------------------------------------------------------ helpers
    def _window(self, tag: str, n: int) -> np.ndarray:
        n = min(n, self.n, HISTORY)
        end = self.n % HISTORY
        col = self.idx[tag]
        if n <= end:
            return self.buf[end - n : end, col]
        return np.concatenate([self.buf[HISTORY - (n - end) :, col], self.buf[:end, col]])

    def shelve(self, rule_id: str, until_ts: int) -> None:
        self.shelved[rule_id] = until_ts

    # ------------------------------------------------------------------ conditions
    def _condition(self, r: Rule, v: dict[str, float], active: bool) -> tuple[bool, float, dict]:
        """Returns (condition true, value to report, evidence)."""
        k = r.kind
        if r.layer == "limit":
            x = v[r.tag]
            if k in ("HI", "HIHI"):
                return (x > r.limit - (r.deadband if active else 0.0)), x, {}
            return (x < r.limit + (r.deadband if active else 0.0)), x, {}
        if k in ("ROC_FALL", "ROC_RISE"):
            if self.n < r.window:
                return False, 0.0, {}
            w = self._window(r.tag, r.window)
            head, tail = w[: max(1, r.window // 10)].mean(), w[-max(1, r.window // 10) :].mean()
            change = tail - head
            hit = -change > r.limit if k == "ROC_FALL" else change > r.limit
            return hit, round(float(change), 3), {"change": round(float(change), 3), "window_min": r.window}
        if r.id == "B-301.DISC":
            return (v["B-301_CMD"] >= 0.5 and v["B-301_RUN"] < 0.5), v["B-301_RUN"], {"command": v["B-301_CMD"]}
        if r.id == "P-202.LOWFLOW":
            sp = v["P-202_SP"]
            return (sp > 1.0 and v["FIT-202"] < r.limit * sp), v["FIT-202"], {"command": round(sp, 2)}
        if r.id == "FV-101.DISC":
            gap = abs(v["FV-101"] - v["ZT-101"])
            return gap > r.limit, v["ZT-101"], {"command": round(v["FV-101"], 1), "gap": round(gap, 1)}
        if k == "FLATLINE":
            # Minutes since the value last moved by at least half a register count (tracked in step()).
            still = self.unchanged[r.tag]
            return still >= r.window, v[r.tag], {"unchanged_min": still}
        if k == "DRIFT":
            if self.n < r.window:
                return False, 0.0, {}
            a = float(self._window("AIT-201", r.window).mean())
            b = float(self._window("AIT-201B", r.window).mean())
            ref = float(self._window("AIT-501", r.window).mean()) - 0.2
            suspect = "AIT-201" if abs(a - ref) > abs(b - ref) else "AIT-201B"
            return (
                abs(a - b) > r.limit,
                round(a - b, 3),
                {"AIT-201": round(a, 2), "AIT-201B": round(b, 2), "suspect": suspect},
            )
        if k == "STUCK":
            if self.n < r.window:
                return False, 0.0, {}
            pos = self._window("ZT-101", r.window)
            cmd = self._window("FV-101", r.window)
            pos_span, cmd_span = float(pos.max() - pos.min()), float(cmd.max() - cmd.min())
            return pos_span < r.limit and cmd_span > 3.0, v["ZT-101"], {"command_span": round(cmd_span, 1)}
        if k == "RANGE":
            return v[r.tag] >= r.limit, v[r.tag], {}
        if k == "PCA":
            t2, spe = self.last_pca
            assert self.pca is not None
            if r.id == "PCA.SPE":
                return (
                    spe > self.pca.spe_limit * self.pca_factor,
                    round(spe, 2),
                    {"limit": round(self.pca.spe_limit, 2)},
                )
            return t2 > self.pca.t2_limit * self.pca_factor, round(t2, 2), {"limit": round(self.pca.t2_limit, 2)}
        raise ValueError(r.id)

    # ------------------------------------------------------------------ main loop
    def step(self, ts: int, values: dict[str, float]) -> StepResult:
        self.buf[self.n % HISTORY] = [values[n] for n in TAG_NAMES]
        self.n += 1
        for tag in FLATLINE_TAGS:
            x = values[tag]
            if abs(x - self.anchor.get(tag, -1e18)) >= 0.5 / BY_NAME[tag].scale:
                self.anchor[tag] = x
                self.unchanged[tag] = 0
            else:
                self.unchanged[tag] += 1
        if self.pca is not None and "anomaly" in self.layers:
            x = self._smooth(np.array([values[t] for t in self.pca.tags]))
            t2, spe = self.pca.statistics(x)
            self.last_pca = (float(t2[0]), float(spe[0]))
            self._last_x = x

        raised: list[Alert] = []
        cleared: list[Alert] = []
        for r in self.rules:
            is_active = r.id in self.active
            hit, value, evidence = self._condition(r, values, is_active)
            if not is_active:
                if hit:
                    self.pending[r.id] = self.pending.get(r.id, 0) + 1
                    if self.pending[r.id] >= r.on_delay:
                        raised.append(self._raise(r, ts, value, evidence))
                        self.pending[r.id] = 0
                else:
                    self.pending[r.id] = 0
            else:
                if not hit:
                    self.clearing[r.id] = self.clearing.get(r.id, 0) + 1
                    if self.clearing[r.id] >= r.off_delay:
                        a = self.active.pop(r.id)
                        a.cleared_ts = ts
                        cleared.append(a)
                        self.clearing[r.id] = 0
                else:
                    self.clearing[r.id] = 0

        self._suppress(raised, ts)
        opened, changed = self._incidents(ts, raised)
        return StepResult(raised, cleared, opened, changed)

    def _raise(self, r: Rule, ts: int, value: float, evidence: dict) -> Alert:
        self._seq += 1
        tag = evidence.get("suspect", r.tag) if r.kind == "DRIFT" else r.tag
        message = r.message
        if r.kind == "DRIFT":
            message = f"{r.message}; {tag} suspected of drifting"
        if r.layer == "anomaly":
            assert self.pca is not None
            contrib = self.pca.contributions(self._last_x)
            top = sorted(contrib.items(), key=lambda kv: -kv[1])[:3]
            evidence = {**evidence, "top_contributors": {k: round(v, 3) for k, v in top}}
            message = f"{r.message}: {', '.join(k for k, _ in top)}"
        area = _area(tag)
        if r.layer == "anomaly" and evidence.get("top_contributors"):
            area = _area(next(iter(evidence["top_contributors"])))
        a = Alert(self._seq, r.id, tag, r.kind, r.layer, r.priority, message, ts, float(value), area, evidence=evidence)
        self.active[r.id] = a
        self.alerts.append(a)
        self.by_id[a.id] = a
        return a

    def _suppress(self, raised: list[Alert], ts: int) -> None:
        while self.annunciated_times and self.annunciated_times[0] <= ts - self.flood_window * 60:
            self.annunciated_times.popleft()
        # Raise higher priorities first so that a flood keeps them visible.
        for a in sorted(raised, key=lambda a: -PRIORITY_RANK[a.priority]):
            if not self.suppression:
                self.annunciated_times.append(ts)
                continue
            parents = SUPPRESSED_BY.get(a.rule_id, ())
            if a.rule_id in self.shelved and self.shelved[a.rule_id] > ts:
                a.suppressed = "shelved"
            elif any(p in self.active for p in parents):
                a.suppressed = "design"
            elif len(self.annunciated_times) >= self.flood_limit and PRIORITY_RANK[a.priority] < PRIORITY_RANK["High"]:
                a.suppressed = "flood"
            else:
                self.annunciated_times.append(ts)

    def _incidents(self, ts: int, raised: list[Alert]) -> tuple[list[Incident], list[Incident]]:
        """Groups alerts into incidents: an alert joins the newest incident if that incident raised something in the
        last ``incident_group`` minutes, otherwise it opens a new one. An incident closes once none of its annunciated
        alerts has been active for ``incident_quiet`` minutes."""
        opened: list[Incident] = []
        changed: list[Incident] = []
        for inc in self.incidents[-10:]:
            if inc.closed_ts is not None:
                continue
            if any(self.by_id[i].active and self.by_id[i].suppressed is None for i in inc.alert_ids):
                inc.last_activity = ts
            elif ts - inc.last_activity >= self.incident_quiet * 60:
                inc.closed_ts = ts
                changed.append(inc)
        for a in raised:
            current = self.incidents[-1] if self.incidents else None
            if current is None or current.closed_ts is not None or ts - current.last_raise > self.incident_group * 60:
                self._inc_seq += 1
                current = Incident(self._inc_seq, ts, a.message, a.area, a.priority, last_activity=ts)
                self.incidents.append(current)
                opened.append(current)
            a.incident_id = current.id
            current.alert_ids.append(a.id)
            current.last_activity = ts
            current.last_raise = ts
            if a.suppressed is None and PRIORITY_RANK[a.priority] > PRIORITY_RANK[current.severity]:
                current.severity = a.priority
            if current not in opened and current not in changed:
                changed.append(current)
        return opened, changed
