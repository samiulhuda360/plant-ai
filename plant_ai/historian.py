"""The plant historian: SQLite time-series tables for tag samples, alarms, incidents and assistant notes.

``samples`` is a narrow (tag, ts, value, quality) table keyed by tag then time, the classic historian layout, so a
trend query for one tag over a time range is a single index range scan. Quality uses OPC conventions
(192 = good, 0 = bad, e.g. communication lost).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .tags import SETPOINTS, TAGS

GOOD = 192
BAD = 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS tags (
  name TEXT PRIMARY KEY, description TEXT, unit TEXT, area TEXT, kind TEXT, lo REAL, hi REAL
);
CREATE TABLE IF NOT EXISTS samples (
  tag TEXT NOT NULL, ts INTEGER NOT NULL, value REAL, quality INTEGER NOT NULL DEFAULT 192,
  PRIMARY KEY (tag, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS alarms (
  id INTEGER PRIMARY KEY, rule_id TEXT, tag TEXT, kind TEXT, layer TEXT, priority TEXT, message TEXT,
  raised_ts INTEGER, cleared_ts INTEGER, acked_ts INTEGER, value REAL, area TEXT, suppressed TEXT,
  incident_id INTEGER, evidence TEXT
);
CREATE INDEX IF NOT EXISTS alarms_raised ON alarms (raised_ts);
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY, opened_ts INTEGER, closed_ts INTEGER, title TEXT, area TEXT, severity TEXT,
  alert_ids TEXT
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, alarm_id INTEGER, mode TEXT, body TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class Historian:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.executemany(
            "INSERT OR IGNORE INTO tags VALUES (?,?,?,?,?,?,?)",
            [(t.name, t.description, t.unit, t.area, t.kind, t.lo, t.hi) for t in TAGS + SETPOINTS],
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ writes
    def write(self, ts: int, values: dict[str, float], quality: int = GOOD, commit: bool = True) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO samples VALUES (?,?,?,?)",
            [(tag, ts, float(v), quality) for tag, v in values.items()],
        )
        if commit:
            self.db.commit()

    def write_many(self, rows: Iterable[tuple[int, dict[str, float]]]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO samples VALUES (?,?,?,?)",
            ((tag, ts, float(v), GOOD) for ts, values in rows for tag, v in values.items()),
        )
        self.db.commit()

    def upsert_alarm(self, a: dict, commit: bool = True) -> None:
        """Inserts a new alarm or updates its clearing; an operator's acknowledgement is never overwritten."""
        self.db.execute(
            """INSERT INTO alarms (id, rule_id, tag, kind, layer, priority, message, raised_ts, cleared_ts, acked_ts,
                 value, area, suppressed, incident_id, evidence)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET cleared_ts=excluded.cleared_ts, incident_id=excluded.incident_id""",
            (
                a["id"],
                a["rule_id"],
                a["tag"],
                a["kind"],
                a["layer"],
                a["priority"],
                a["message"],
                a["raised_ts"],
                a["cleared_ts"],
                a["acked_ts"],
                a["value"],
                a["area"],
                a["suppressed"],
                a["incident_id"],
                json.dumps(a.get("evidence") or {}),
            ),
        )
        if commit:
            self.db.commit()

    def upsert_incident(self, i: dict, commit: bool = True) -> None:
        self.db.execute(
            """INSERT INTO incidents VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET closed_ts=excluded.closed_ts, severity=excluded.severity,
                 alert_ids=excluded.alert_ids""",
            (i["id"], i["opened_ts"], i["closed_ts"], i["title"], i["area"], i["severity"], json.dumps(i["alert_ids"])),
        )
        if commit:
            self.db.commit()

    def ack(self, alarm_id: int, ts: int) -> bool:
        cur = self.db.execute("UPDATE alarms SET acked_ts=? WHERE id=? AND acked_ts IS NULL", (ts, alarm_id))
        self.db.commit()
        return cur.rowcount > 0

    def add_note(self, ts: int, alarm_id: int | None, mode: str, body: dict) -> int:
        cur = self.db.execute(
            "INSERT INTO notes (ts, alarm_id, mode, body) VALUES (?,?,?,?)", (ts, alarm_id, mode, json.dumps(body))
        )
        self.db.commit()
        return int(cur.lastrowid or 0)

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
        self.db.commit()

    def commit(self) -> None:
        self.db.commit()

    # ------------------------------------------------------------------ reads
    def meta(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def last_ts(self) -> int | None:
        row = self.db.execute("SELECT MAX(ts) FROM samples WHERE tag='FIT-101'").fetchone()
        return row[0] if row and row[0] is not None else None

    def first_ts(self) -> int | None:
        row = self.db.execute("SELECT MIN(ts) FROM samples WHERE tag='FIT-101'").fetchone()
        return row[0] if row and row[0] is not None else None

    def latest(self) -> dict[str, dict]:
        rows = self.db.execute(
            """SELECT s.tag, s.ts, s.value, s.quality FROM samples s
               JOIN (SELECT tag, MAX(ts) AS ts FROM samples GROUP BY tag) m ON s.tag=m.tag AND s.ts=m.ts"""
        ).fetchall()
        return {tag: {"ts": ts, "value": value, "quality": q} for tag, ts, value, q in rows}

    def series(self, tag: str, start: int, end: int) -> list[tuple[int, float]]:
        return self.db.execute(
            "SELECT ts, value FROM samples WHERE tag=? AND ts>=? AND ts<=? ORDER BY ts", (tag, start, end)
        ).fetchall()

    def frame(self, tags: list[str], start: int, end: int) -> tuple[list[int], dict[str, list[float]]]:
        """Aligned columns for several tags (rows where a tag is missing hold NaN)."""
        rows = self.db.execute(
            f"SELECT ts, tag, value FROM samples WHERE tag IN ({','.join('?' * len(tags))}) AND ts>=? AND ts<=?",
            (*tags, start, end),
        ).fetchall()
        times = sorted({r[0] for r in rows})
        pos = {t: i for i, t in enumerate(times)}
        cols = {t: [float("nan")] * len(times) for t in tags}
        for ts, tag, value in rows:
            cols[tag][pos[ts]] = value
        return times, cols

    def alarms(self, start: int | None = None, end: int | None = None, limit: int = 500) -> list[dict]:
        q = "SELECT * FROM alarms"
        args: list = []
        if start is not None and end is not None:
            q += " WHERE raised_ts>=? AND raised_ts<=?"
            args = [start, end]
        q += " ORDER BY raised_ts DESC, id DESC LIMIT ?"
        args.append(limit)
        return [self._alarm_row(r) for r in self.db.execute(q, args).fetchall()]

    def active_alarms(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM alarms WHERE cleared_ts IS NULL OR acked_ts IS NULL ORDER BY raised_ts DESC"
        ).fetchall()
        return [self._alarm_row(r) for r in rows]

    def alarm(self, alarm_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM alarms WHERE id=?", (alarm_id,)).fetchone()
        return self._alarm_row(row) if row else None

    def incidents(self, limit: int = 50, start: int | None = None, end: int | None = None) -> list[dict]:
        q, args = "SELECT * FROM incidents", []
        if start is not None and end is not None:
            q += " WHERE opened_ts<=? AND (closed_ts IS NULL OR closed_ts>=?)"
            args = [end, start]
        rows = self.db.execute(q + " ORDER BY opened_ts DESC LIMIT ?", (*args, limit)).fetchall()
        return [
            {
                "id": r[0],
                "opened_ts": r[1],
                "closed_ts": r[2],
                "title": r[3],
                "area": r[4],
                "severity": r[5],
                "alert_ids": json.loads(r[6] or "[]"),
                "status": "open" if r[2] is None else "closed",
            }
            for r in rows
        ]

    def notes(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute("SELECT id, ts, alarm_id, mode, body FROM notes ORDER BY id DESC LIMIT ?", (limit,))
        return [{"id": r[0], "ts": r[1], "alarm_id": r[2], "mode": r[3], "body": json.loads(r[4])} for r in rows]

    @staticmethod
    def _alarm_row(r: tuple) -> dict:
        keys = (
            "id",
            "rule_id",
            "tag",
            "kind",
            "layer",
            "priority",
            "message",
            "raised_ts",
            "cleared_ts",
            "acked_ts",
            "value",
            "area",
            "suppressed",
            "incident_id",
            "evidence",
        )
        d = dict(zip(keys, r, strict=True))
        d["evidence"] = json.loads(d["evidence"] or "{}")
        if d["cleared_ts"] is None:
            d["state"] = "ACTIVE_ACK" if d["acked_ts"] else "ACTIVE_UNACK"
        else:
            d["state"] = "CLEARED" if d["acked_ts"] else "RTN_UNACK"
        return d
