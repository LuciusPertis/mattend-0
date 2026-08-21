"""The local dump: UUID | CID | Gen_T | Cap_T, plus the verdict that produced it.

One row per (uuid, c_id) -- a student scanning twice updates their row rather
than adding a second one, so a jittery camera reading the same phone across
several frames cannot inflate the count.
"""

from __future__ import annotations

import csv
import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attendance (
    uuid        TEXT NOT NULL,
    c_id        INTEGER NOT NULL,
    gen_t       INTEGER NOT NULL,
    cap_t       INTEGER NOT NULL,
    delta_t     INTEGER NOT NULL,
    verdict     TEXT NOT NULL,
    course_cid  TEXT NOT NULL,
    email       TEXT,
    name        TEXT,
    roll        TEXT,
    scanned_at  INTEGER NOT NULL,
    scan_count  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (uuid, c_id)
);
CREATE INDEX IF NOT EXISTS attendance_course ON attendance (course_cid, verdict);
"""


class Store:
    def __init__(self, db_path: Path | str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def record(self, result) -> None:
        """Upsert one verification result. `result` is a verify.Result."""
        relay = result.relay
        student = result.student
        self.conn.execute(
            """
            INSERT INTO attendance
                (uuid, c_id, gen_t, cap_t, delta_t, verdict, course_cid, email, name, roll, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uuid, c_id) DO UPDATE SET
                cap_t      = excluded.cap_t,
                delta_t    = excluded.delta_t,
                gen_t      = excluded.gen_t,
                scanned_at = excluded.scanned_at,
                scan_count = attendance.scan_count + 1,
                -- never let a later timeout downgrade an already-good mark
                verdict    = CASE WHEN attendance.verdict = 'OK' THEN 'OK' ELSE excluded.verdict END
            """,
            (
                relay.device_uuid,
                relay.c_id,
                relay.gen_t,
                relay.cap_t,
                relay.delta_t,
                result.verdict,
                result.course_cid,
                student.email if student else None,
                student.name if student else None,
                student.roll if student else None,
                int(time.time()),
            ),
        )
        self.conn.commit()

    def present(self, course_cid: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM attendance WHERE course_cid = ? AND verdict = 'OK' ORDER BY scanned_at",
                (course_cid,),
            )
        )

    def all_rows(self, course_cid: str | None = None) -> list[sqlite3.Row]:
        if course_cid:
            return list(
                self.conn.execute(
                    "SELECT * FROM attendance WHERE course_cid = ? ORDER BY scanned_at", (course_cid,)
                )
            )
        return list(self.conn.execute("SELECT * FROM attendance ORDER BY scanned_at"))

    def export_csv(self, out_path: Path | str, course_cid: str | None = None) -> Path:
        rows = self.all_rows(course_cid)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        columns = [
            "roll", "name", "email", "verdict", "course_cid",
            "uuid", "c_id", "gen_t", "cap_t", "delta_t", "scan_count", "scanned_at",
        ]
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([row[c] for c in columns])
        return out

    def close(self) -> None:
        self.conn.close()
