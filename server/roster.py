"""Registration roster, loaded from a Google Form responses CSV export.

The real export is messier than it looks, so this loader is deliberately
defensive about three things:

1.  **More than one header row.** The sheet can carry a hand-written label row
    above the form's own header, and the two can disagree about column *order*.
    Trusting the first one silently loads names as UUIDs and everybody fails
    verification. So the header is found by position instead: the last row
    before the first row that contains an email address.

2.  **Column names drift.** `UUID` became `msL-key`, `CID` became `Class_ID`.
    Matching is fuzzy and alias-based rather than exact.

3.  **Junk in the UUID column.** A student can submit anything. Rows whose UUID
    is not a real UUID are dropped and counted -- if they were kept they would
    never match a device, but they *would* inflate that email's device count and
    wrongly flag the student as MUoP.

Two indexes come out of it, matching the verification diagram:

    uuid  -> student        absent  => UNF  (user not found)
    email -> {uuid, ...}    len > 1 => MUoP (one person, several devices)
"""

from __future__ import annotations

import csv
import re
import uuid as uuidlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_HEADER_ALIASES = {
    "timestamp": ("timestamp", "time stamp", "submitted"),
    "email": ("email", "e-mail", "mail"),
    "uuid": ("uuid", "uvid", "msl-key", "msl key", "-key", "device"),
    "name": ("name",),
    "cid": ("cid", "class", "course", "section"),
}

# Checked in this order so the specific "msL-key" wins before a looser match.
_ALIAS_ORDER = ("timestamp", "email", "uuid", "name", "cid")

# Google Forms exports in the sheet's locale, so a slashed date can be either
# M/D/Y or D/M/Y and the file does not say which. Rather than guess, the order
# is inferred from the data -- see _infer_date_order. None of these formats are
# required: an unparseable stamp yields None, never an error.
_ISO_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
_SLASH_FORMATS = ("{0} %H:%M:%S", "{0} %H:%M", "{0} %I:%M:%S %p", "{0} %I:%M %p")
_SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})")

MONTH_FIRST = "M/D/Y"
DAY_FIRST = "D/M/Y"
AMBIGUOUS = "M/D/Y (assumed)"


def _infer_date_order(stamps: list[str]) -> str:
    """Work out whether slashed dates are M/D/Y or D/M/Y from the data itself.

    A component greater than 12 cannot be a month, so one such row settles it
    for the whole file. With no discriminating row the two readings agree only
    by luck, so we say so rather than pretending to know.
    """
    month_first = day_first = False
    for raw in stamps:
        match = _SLASH_DATE.match(raw.strip())
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if second > 12:
            month_first = True
        if first > 12:
            day_first = True
    if month_first and not day_first:
        return MONTH_FIRST
    if day_first and not month_first:
        return DAY_FIRST
    return AMBIGUOUS


def _formats_for(order: str) -> tuple[str, ...]:
    date = "%d/%m/%Y" if order == DAY_FIRST else "%m/%d/%Y"
    return tuple(f.format(date) for f in _SLASH_FORMATS) + _ISO_FORMATS

# e.g. "iec2026025" -> "IEC 2026 025", the roll format on the verdict cards.
_ROLL = re.compile(r"^([a-zA-Z]{2,5})[\-_]?(\d{4})[\-_]?(\d{1,4})$")


def parse_timestamp(raw: str, order: str = MONTH_FIRST) -> datetime | None:
    raw = (raw or "").strip()
    for fmt in _formats_for(order):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def normalise_uuid(raw: str) -> str | None:
    """Canonical lowercase UUID, or None if it isn't one."""
    try:
        return str(uuidlib.UUID((raw or "").strip()))
    except (ValueError, AttributeError, TypeError):
        return None


@dataclass(frozen=True)
class Student:
    uuid: str
    email: str
    name: str
    cid: str
    registered_at: datetime | None = None

    @property
    def roll(self) -> str:
        local = self.email.split("@", 1)[0].strip()
        match = _ROLL.match(local)
        if match:
            return " ".join(match.groups()).upper()
        return local.upper() or self.name.upper()


@dataclass(frozen=True)
class RejectedRow:
    line: int
    reason: str
    raw: str


class RosterError(RuntimeError):
    pass


def _map_headers(fieldnames: list[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for key in _ALIAS_ORDER:
        for column in fieldnames:
            if column in resolved.values():
                continue
            if any(alias in column.strip().lower() for alias in _HEADER_ALIASES[key]):
                resolved[key] = column
                break
    missing = {"email", "name", "uuid", "cid"} - resolved.keys()
    if missing:
        raise RosterError(
            f"roster CSV has no column for {sorted(missing)}; saw headers {fieldnames}. "
            f"Add an alias in roster._HEADER_ALIASES if the form was renamed."
        )
    return resolved


def _split_header(rows: list[list[str]]) -> tuple[list[str], list[list[str]], int]:
    """Find the real header: the last row before the first row holding an email.

    A stale label row above the form's own header would otherwise be taken as
    the header, and the two can list the columns in a different order.
    """
    for index, row in enumerate(rows):
        if any("@" in cell for cell in row):
            if index == 0:
                raise RosterError("roster CSV has data on line 1 but no header row")
            return rows[index - 1], rows[index:], index - 1
    raise RosterError("roster CSV has no rows containing an email address")


class Roster:
    """Roster filtered to one course CID."""

    def __init__(self, students: list[Student], course_cid: str, source: Path | None = None,
                 rejected: list[RejectedRow] | None = None, header_line: int = 0,
                 date_order: str = MONTH_FIRST):
        self.course_cid = course_cid
        self.source = source
        self.rejected = rejected or []
        self.header_line = header_line
        self.date_order = date_order
        # Oldest first, so a re-registration of the same device overwrites the
        # stale row below. Rows with no parseable stamp sort first, keeping
        # their relative file order.
        self.students = sorted(students, key=lambda s: (s.registered_at is not None,
                                                        s.registered_at or datetime.min))
        self._by_uuid: dict[str, Student] = {}
        self._by_email: dict[str, list[str]] = defaultdict(list)
        for student in self.students:
            self._by_uuid[student.uuid] = student
            if student.uuid not in self._by_email[student.email]:
                self._by_email[student.email].append(student.uuid)

    def __len__(self) -> int:
        return len(self._by_uuid)

    def lookup(self, device_uuid: str) -> Student | None:
        key = normalise_uuid(device_uuid)
        return self._by_uuid.get(key) if key else None

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

    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = [row for row in csv.reader(handle) if any(cell.strip() for cell in row)]
    if not rows:
        raise RosterError(f"roster CSV at {path} is empty")

    header, data, header_line = _split_header(rows)
    cols = _map_headers(header)
    index_of = {key: header.index(column) for key, column in cols.items()}

    def cell(row: list[str], key: str) -> str:
        position = index_of.get(key, -1)
        return row[position].strip() if 0 <= position < len(row) else ""

    want_cid = course_cid.strip().upper()
    date_order = _infer_date_order([cell(row, "timestamp") for row in data])
    students: list[Student] = []
    rejected: list[RejectedRow] = []

    for offset, row in enumerate(data):
        line = header_line + 2 + offset      # 1-based line number in the file
        if cell(row, "cid").upper() != want_cid:
            continue
        device_uuid = normalise_uuid(cell(row, "uuid"))
        if device_uuid is None:
            rejected.append(RejectedRow(line, "UUID is not a valid UUID", ",".join(row)[:90]))
            continue
        email = cell(row, "email").lower()
        if "@" not in email:
            rejected.append(RejectedRow(line, "no email address", ",".join(row)[:90]))
            continue
        students.append(
            Student(
                uuid=device_uuid,
                email=email,
                name=cell(row, "name"),
                cid=cell(row, "cid"),
                registered_at=parse_timestamp(cell(row, "timestamp"), date_order),
            )
        )

    return Roster(students, want_cid, source=path, rejected=rejected,
                  header_line=header_line, date_order=date_order)
