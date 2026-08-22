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

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from .classroom import (
    Classroom,
    Registry,
    REGISTRY_NAME,
    derive_cid,
    migrate_from_config,
)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

__all__ = ["Config", "Session", "ConfigError", "derive_cid", "load"]


class ConfigError(RuntimeError):
    pass


@dataclass
class Session:
    """A fixed session. `Classroom` is the real thing now -- this survives for
    tests and for anything that wants a session without a registry entry."""

    course_cid: str
    date: str
    slot: str = "A"
    label: str = ""

    @property
    def resolved_date(self) -> str:
        return self.date

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
    submit_window_seconds: int = 10
    capture_window_seconds: int = 120
    require_signature: bool = False
    rotate_key_on_launch: bool = True
    clock_skew_tolerance_seconds: int = 15
    qr_rotate_seconds: int = 5
    roster_csv: str = "data/responses.csv"
    db_path: str = "data/attendance.sqlite3"
    camera_index: int = 0
    pwa_url: str = ""
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent)
    registry: Registry | None = None

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


def bootstrap(path: str | os.PathLike | None = None, pwa_url: str = "") -> tuple[Path, bool]:
    """Make sure this teacher has a config.json with their own secrets.

    Returns (path, created). The pc_secret minted here is theirs alone: source
    QRs made with it verify only on their own station.
    """
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if config_path.exists():
        return config_path, False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "pc_secret_hex": secrets.token_hex(32),
                "app_secret_hex": secrets.token_hex(32),
                "pwa_url": pwa_url,
                "db_path": "data/attendance.sqlite3",
                "camera_index": 0,
            },
            indent=2,
        )
        + "\n"
    )
    return config_path, True


def update_raw(path: str | os.PathLike, **changes) -> None:
    """Patch individual keys in config.json, leaving the rest untouched."""
    config_path = Path(path)
    raw = json.loads(config_path.read_text()) if config_path.exists() else {}
    raw.update(changes)
    config_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")


def load(path: str | os.PathLike | None = None, class_key: str | None = None) -> Config:
    """Teacher settings from config.json, class settings from classes.json.

    Everything about *a class* now lives in the registry; config.json keeps only
    what belongs to this teacher and this machine.
    """
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(f"no config at {config_path}. Copy config.example.json and fill it in.")
    raw = json.loads(config_path.read_text())
    base_dir = config_path.resolve().parent

    registry = Registry.load(base_dir / REGISTRY_NAME)
    migrate_from_config(registry, config_path)     # fold a pre-registry session block in

    if class_key:
        room = registry.get(class_key)
        if room is None:
            known = ", ".join(sorted(registry.classes)) or "none yet"
            raise ConfigError(f"no class named {class_key!r}. Known classes: {known}")
    else:
        room = registry.active
    if room is None:
        raise ConfigError(
            "no classes defined yet. Run `python3 -m server.admin` to add one."
        )

    return Config(
        pc_secret=_secret(raw, "pc_secret_hex", "MATTEND_PC_SECRET"),
        app_secret=_secret(raw, "app_secret_hex", "MATTEND_APP_SECRET"),
        session=room,
        delta_t_max_seconds=room.delta_t_max_seconds,
        submit_window_seconds=room.submit_window_seconds,
        capture_window_seconds=room.capture_window_seconds,
        require_signature=room.require_signature,
        rotate_key_on_launch=raw.get("rotate_key_on_launch", True),
        clock_skew_tolerance_seconds=room.clock_skew_tolerance_seconds,
        qr_rotate_seconds=room.qr_rotate_seconds,
        roster_csv=room.roster_csv,
        db_path=raw.get("db_path", "data/attendance.sqlite3"),
        camera_index=raw.get("camera_index", 0),
        pwa_url=raw.get("pwa_url", ""),
        base_dir=base_dir,
        registry=registry,
    )


if __name__ == "__main__":
    import sys

    if "--new-secrets" in sys.argv:
        print(f'  "pc_secret_hex":  "{secrets.token_hex(32)}",')
        print(f'  "app_secret_hex": "{secrets.token_hex(32)}",')
        print("\n# pc_secret stays on the two lab PCs.")
        print("# pc_secret is yours alone -- your source QRs only verify on your station.")
        print("# app_secret is also baked into docs/protocol.js (APP_SECRET_HEX).")
    else:
        cfg = load()
        print(f"classes : {len(cfg.registry)} in {cfg.registry.path.name}")
        print(f"session : {cfg.session.display}")
        print(f"C_ID    : 0x{cfg.session.c_id:08x}")
        print(f"roster  : {cfg.roster_path}")
        print(f"db      : {cfg.db_file}")
        print(f"ΔT max  : {cfg.delta_t_max_seconds}s (skew tolerance {cfg.clock_skew_tolerance_seconds}s)")
