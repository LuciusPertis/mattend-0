"""Configuration and session identity.

C_ID is the "space" axis of the diagram: lab/class/day collapsed into one
32-bit opaque value. It is opaque on purpose -- PC (in) never inverts it back
into a course string, it just recomputes the value it expects locally and
checks equality (the "C_ID == match sanity check" in the verification diagram).
That is what stops a QR photographed in another room, or yesterday, from
verifying here.

The roster lookup uses `course_cid` directly, since that is the string students
type into the registration form's CID field.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


class ConfigError(RuntimeError):
    pass


def derive_cid(course_cid: str, date: str, slot: str) -> int:
    """32-bit session id from (lab/class, day, slot)."""
    canonical = f"{course_cid.strip().upper()}|{date.strip()}|{slot.strip().upper()}"
    return int.from_bytes(hashlib.blake2s(canonical.encode(), digest_size=4).digest(), "big")


@dataclass
class Session:
    course_cid: str
    date: str
    slot: str = "A"
    label: str = ""

    @property
    def c_id(self) -> int:
        return derive_cid(self.course_cid, self.date, self.slot)

    @property
    def display(self) -> str:
        return self.label or f"{self.course_cid} · {self.date} · slot {self.slot}"


@dataclass
class Config:
    pc_secret: bytes
    app_secret: bytes
    session: Session
    delta_t_max_seconds: int = 90
    clock_skew_tolerance_seconds: int = 15
    qr_rotate_seconds: int = 5
    roster_csv: str = "data/responses.csv"
    db_path: str = "data/attendance.sqlite3"
    camera_index: int = 0
    card_ttl_seconds: int = 6
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent)

    def path(self, value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else (self.base_dir / p)

    @property
    def roster_path(self) -> Path:
        return self.path(self.roster_csv)

    @property
    def db_file(self) -> Path:
        return self.path(self.db_path)


def _secret(raw: dict, key: str, env: str) -> bytes:
    value = os.environ.get(env) or raw.get(key)
    if not value:
        raise ConfigError(
            f"missing {key!r} (or ${env}). Run `python -m server.config --new-secrets` to mint a pair."
        )
    try:
        data = bytes.fromhex(value)
    except ValueError as exc:
        raise ConfigError(f"{key!r} must be hex: {exc}") from exc
    if len(data) != 32:
        raise ConfigError(f"{key!r} must be 32 bytes (64 hex chars), got {len(data)}")
    return data


def load(path: str | os.PathLike | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(f"no config at {config_path}. Copy config.example.json and fill it in.")
    raw = json.loads(config_path.read_text())

    session_raw = raw.get("session") or {}
    for required in ("course_cid", "date"):
        if not session_raw.get(required):
            raise ConfigError(f"config session.{required} is required")

    known = {f for f in Config.__dataclass_fields__ if f not in {"pc_secret", "app_secret", "session", "base_dir"}}
    extras = {k: v for k, v in raw.items() if k in known}

    return Config(
        pc_secret=_secret(raw, "pc_secret_hex", "MATTEND_PC_SECRET"),
        app_secret=_secret(raw, "app_secret_hex", "MATTEND_APP_SECRET"),
        session=Session(
            course_cid=session_raw["course_cid"],
            date=session_raw["date"],
            slot=session_raw.get("slot", "A"),
            label=session_raw.get("label", ""),
        ),
        base_dir=config_path.resolve().parent,
        **extras,
    )


if __name__ == "__main__":
    import sys

    if "--new-secrets" in sys.argv:
        print(f'  "pc_secret_hex":  "{secrets.token_hex(32)}",')
        print(f'  "app_secret_hex": "{secrets.token_hex(32)}",')
        print("\n# pc_secret stays on the two lab PCs.")
        print("# app_secret is also baked into docs/protocol.js (APP_SECRET_HEX).")
    else:
        cfg = load()
        print(f"session : {cfg.session.display}")
        print(f"C_ID    : 0x{cfg.session.c_id:08x}")
        print(f"roster  : {cfg.roster_path}")
        print(f"db      : {cfg.db_file}")
        print(f"ΔT max  : {cfg.delta_t_max_seconds}s (skew tolerance {cfg.clock_skew_tolerance_seconds}s)")
