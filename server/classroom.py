"""Classes, and the registry that holds them.

One `config.json` per teacher carries the things that belong to *them* -- their
`pc_secret`, their camera, where the PWA lives. `classes.json` holds the classes
they teach. Nothing about a class is compiled in any more.

The awkward part of setting this up is Google Forms' `entry.954365518` field
ids, which are not shown anywhere in the form editor. They *are* in the URL that
"Get prefilled link" produces, so `parse_prefilled_link` pulls them out of that
and the teacher never has to see one.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date as date_cls
from pathlib import Path

REGISTRY_NAME = "classes.json"

# https://docs.google.com/forms/d/e/<id>/viewform  (also matches /forms/d/<id>/)
_FORM_ID = re.compile(r"/forms/d/(?:e/)?([A-Za-z0-9_-]{10,})")
_ENTRY = re.compile(r"entry\.(\d+)")

# Sentinel text a teacher can type into the prefill so roles are auto-assigned.
_ROLE_HINTS = {
    "uuid": ("uuid", "key", "device", "msl"),
    "name": ("name",),
    "cid": ("cid", "class", "course", "section"),
}

TODAY = "today"


class ClassroomError(RuntimeError):
    pass


def derive_cid(course_cid: str, date: str, slot: str) -> int:
    """32-bit session id from (lab/class, day, slot).

    Opaque on purpose: PC (in) never inverts it, it recomputes the value it
    expects and checks equality. That is what stops a QR photographed in another
    room, or yesterday, from verifying here.
    """
    canonical = f"{course_cid.strip().upper()}|{date.strip()}|{slot.strip().upper()}"
    return int.from_bytes(hashlib.blake2s(canonical.encode(), digest_size=4).digest(), "big")


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "class"


def parse_prefilled_link(url: str) -> tuple[str, dict[str, str]]:
    """Pull the form id and every entry id out of a "Get prefilled link" URL.

    Returns (form_id, {entry_id: the sample value the teacher typed}). The sample
    values are what let `guess_roles` work out which field is which.
    """
    url = (url or "").strip()
    if not url:
        raise ClassroomError("paste the prefilled link first")
    match = _FORM_ID.search(url)
    if not match:
        raise ClassroomError(
            "that does not look like a Google Form link -- it should contain /forms/d/e/..."
        )
    form_id = match.group(1)

    entries: dict[str, str] = {}
    query = url.split("?", 1)[1] if "?" in url else ""
    for pair in query.split("&"):
        if "=" not in pair:
            continue
        raw_key, _, raw_value = pair.partition("=")
        entry = _ENTRY.fullmatch(raw_key.strip())
        if entry:
            from urllib.parse import unquote_plus

            entries[entry.group(1)] = unquote_plus(raw_value)
    if not entries:
        raise ClassroomError(
            "no entry.NNN fields in that link. Use Google Forms' 'Get prefilled link', "
            "fill every question, then Get link."
        )
    return form_id, entries


def guess_roles(entries: dict[str, str]) -> dict[str, str]:
    """Map role -> entry id, using whatever the teacher typed as the sample."""
    roles: dict[str, str] = {}
    for role, hints in _ROLE_HINTS.items():
        for entry_id, sample in entries.items():
            if entry_id in roles.values():
                continue
            if any(hint in sample.strip().lower() for hint in hints):
                roles[role] = entry_id
                break
    return roles


@dataclass
class Classroom:
    key: str
    course_cid: str
    label: str = ""
    form_id: str = ""
    entries: dict[str, str] = field(default_factory=dict)   # role -> entry id
    roster_csv: str = ""
    slot: str = "A"
    date: str = TODAY                       # "today" resolves per run, so nobody edits it daily
    delta_t_max_seconds: int = 15
    clock_skew_tolerance_seconds: int = 5
    qr_rotate_seconds: int = 4

    def __post_init__(self):
        self.course_cid = self.course_cid.strip().upper()
        if not self.label:
            self.label = self.course_cid
        if not self.roster_csv:
            self.roster_csv = f"data/{self.key}.csv"

    @property
    def resolved_date(self) -> str:
        return date_cls.today().isoformat() if self.date == TODAY else self.date

    @property
    def c_id(self) -> int:
        return derive_cid(self.course_cid, self.resolved_date, self.slot)

    @property
    def display(self) -> str:
        return f"{self.label} · {self.resolved_date} · slot {self.slot}"

    @property
    def ready(self) -> tuple[bool, str]:
        """Whether this class can produce an enrollment QR yet."""
        if not self.course_cid:
            return False, "no course CID"
        if not self.form_id:
            return False, "no Google Form linked"
        if "uuid" not in self.entries:
            return False, "the form's device-id field is not assigned"
        if "name" not in self.entries:
            return False, "the form's name field is not assigned"
        return True, "ready"

    def to_json(self) -> dict:
        data = asdict(self)
        data.pop("key")
        return data


class Registry:
    """All of a teacher's classes, plus which one is active."""

    def __init__(self, path: Path, classes: dict[str, Classroom], active: str | None = None):
        self.path = Path(path)
        self.classes = classes
        self._active = active if active in classes else (next(iter(classes), None))

    # ----- persistence -----

    @classmethod
    def load(cls, path: Path | str) -> "Registry":
        path = Path(path)
        if not path.exists():
            return cls(path, {})
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ClassroomError(f"{path} is not valid JSON: {exc}") from exc
        classes = {
            key: Classroom(key=key, **body) for key, body in (raw.get("classes") or {}).items()
        }
        return cls(path, classes, raw.get("active"))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active": self._active,
            "classes": {key: room.to_json() for key, room in self.classes.items()},
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    # ----- access -----

    def __len__(self) -> int:
        return len(self.classes)

    def __iter__(self):
        return iter(sorted(self.classes.values(), key=lambda room: room.label.lower()))

    def get(self, key: str) -> Classroom | None:
        return self.classes.get(key)

    @property
    def active(self) -> Classroom | None:
        return self.classes.get(self._active) if self._active else None

    def set_active(self, key: str) -> None:
        if key not in self.classes:
            raise ClassroomError(f"no class named {key!r}")
        self._active = key

    def add(self, room: Classroom) -> Classroom:
        room.key = self.unique_key(room.key)
        self.classes[room.key] = room
        if self._active is None:
            self._active = room.key
        return room

    def rename(self, old: str, new_base: str) -> str:
        """Re-key a class, e.g. once a placeholder gets its real course code."""
        room = self.classes.pop(old, None)
        if room is None:
            return old
        room.key = self.unique_key(new_base)
        self.classes[room.key] = room
        if self._active == old:
            self._active = room.key
        return room.key

    def remove(self, key: str) -> None:
        self.classes.pop(key, None)
        if self._active == key:
            self._active = next(iter(self.classes), None)

    def unique_key(self, base: str) -> str:
        key = slugify(base)
        if key not in self.classes:
            return key
        suffix = 2
        while f"{key}-{suffix}" in self.classes:
            suffix += 1
        return f"{key}-{suffix}"


def migrate_from_config(registry: Registry, config_path: Path) -> Classroom | None:
    """Fold a pre-registry config.json `session` block into the registry.

    Existing setups keep working without the teacher retyping anything.
    """
    if len(registry) or not config_path.exists():
        return None
    try:
        raw = json.loads(config_path.read_text())
    except json.JSONDecodeError:
        return None
    session = raw.get("session") or {}
    if not session.get("course_cid"):
        return None
    room = Classroom(
        key=slugify(session["course_cid"]),
        course_cid=session["course_cid"],
        label=session.get("label", ""),
        slot=session.get("slot", "A"),
        roster_csv=raw.get("roster_csv", ""),
        delta_t_max_seconds=raw.get("delta_t_max_seconds", 15),
        clock_skew_tolerance_seconds=raw.get("clock_skew_tolerance_seconds", 5),
        qr_rotate_seconds=raw.get("qr_rotate_seconds", 4),
    )
    registry.add(room)
    registry.save()
    return room
