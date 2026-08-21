"""Registration roster, loaded from a Google Form responses CSV export.

Expected columns (header matching is fuzzy, order-independent):

    Timestamp | Email (IITA) | Name | UUID | CID

Two indexes come out of it, matching the verification diagram:

    uuid  -> student        absent  => UNF  (user not found)
    email -> {uuid, ...}    len > 1 => MUoP (one person, several devices)

MUoP is the proxy signal: a student who registered a second device is very
likely holding a friend's phone. It is reported, not silently dropped -- the
card still names them.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_HEADER_ALIASES = {
    "timestamp": ("timestamp", "time stamp"),
    "email": ("email", "e-mail", "mail"),
    "name": ("name",),
    "uuid": ("uuid", "uvid", "device"),
    "cid": ("cid", "class", "course"),
}

# e.g. "iec2026025" -> "IEC 2026 025", the roll format on the verdict cards.
_ROLL = re.compile(r"^([a-zA-Z]{2,5})[\-_]?(\d{4})[\-_]?(\d{1,4})$")


@dataclass(frozen=True)
class Student:
    uuid: str
    email: str
    name: str
    cid: str

    @property
    def roll(self) -> str:
        local = self.email.split("@", 1)[0].strip()
        match = _ROLL.match(local)
        if match:
            return " ".join(match.groups()).upper()
        return local.upper() or self.name.upper()


class RosterError(RuntimeError):
    pass


def _map_headers(fieldnames: list[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for column in fieldnames or []:
        low = column.strip().lower()
        for key, aliases in _HEADER_ALIASES.items():
            if key in resolved:
                continue
            if any(alias in low for alias in aliases):
                resolved[key] = column
                break
    missing = {"email", "name", "uuid", "cid"} - resolved.keys()
    if missing:
        raise RosterError(
            f"roster CSV is missing column(s) for {sorted(missing)}; saw headers {fieldnames}"
        )
    return resolved


class Roster:
    """Roster filtered to one course CID."""

    def __init__(self, students: list[Student], course_cid: str, source: Path | None = None):
        self.course_cid = course_cid
        self.source = source
        self.students = students
        self._by_uuid: dict[str, Student] = {}
        self._by_email: dict[str, list[str]] = defaultdict(list)
        for student in students:
            # Later rows win: a student who re-registers overwrites the stale row,
            # but the email index keeps every distinct UUID so MUoP still fires.
            self._by_uuid[student.uuid] = student
            if student.uuid not in self._by_email[student.email]:
                self._by_email[student.email].append(student.uuid)

    def __len__(self) -> int:
        return len(self._by_uuid)

    def lookup(self, device_uuid: str) -> Student | None:
        return self._by_uuid.get(device_uuid.strip().lower())

    def devices_for(self, email: str) -> list[str]:
        return list(self._by_email.get(email, []))

    def is_muop(self, student: Student) -> bool:
        return len(self._by_email.get(student.email, [])) > 1

    def muop_report(self) -> dict[str, list[str]]:
        """Every email with more than one registered device."""
        return {email: uuids for email, uuids in self._by_email.items() if len(uuids) > 1}


def load(csv_path: Path | str, course_cid: str) -> Roster:
    path = Path(csv_path)
    if not path.exists():
        raise RosterError(
            f"no roster CSV at {path}. Export the Google Form responses to CSV and drop it there."
        )
    students: list[Student] = []
    want_cid = course_cid.strip().upper()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        cols = _map_headers(reader.fieldnames or [])
        for row in reader:
            cid = (row.get(cols["cid"]) or "").strip()
            if cid.upper() != want_cid:
                continue
            raw_uuid = (row.get(cols["uuid"]) or "").strip().lower()
            if not raw_uuid:
                continue
            students.append(
                Student(
                    uuid=raw_uuid,
                    email=(row.get(cols["email"]) or "").strip().lower(),
                    name=(row.get(cols["name"]) or "").strip(),
                    cid=cid,
                )
            )
    return Roster(students, want_cid, source=path)
