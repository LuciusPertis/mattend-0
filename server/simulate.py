"""End-to-end dry run with no camera and no phone.

Walks a payload through both hops in software and prints the verdict for each
of the six outcomes, so the pipeline can be checked before anyone stands in
front of a webcam.

    python -m server.simulate               all six cases
    python -m server.simulate --png         also write QR pngs to data/
"""

from __future__ import annotations

import secrets
import sys
import time
from pathlib import Path

from . import config as config_mod
from . import keys as keys_mod
from . import roster as roster_mod
from . import verify as verify_mod
from .classroom import Classroom, derive_cid
from .config import Config
from .protocol import make_response_qr, make_source_qr
from .verify import Verifier

DEMO_CID = "DEMO-101"
DEMO_ROSTER = "data/responses.sample.csv"

UNREGISTERED = "99999999-9999-4999-8999-999999999999"


def _write_png(text: str, path) -> None:
    import qrcode

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(path)
    print(f"    wrote {path} (QR v{qr.version}, {len(text)} chars)")


def demo_config() -> Config:
    """A throwaway class, so a fresh clone can run this before anything is set up."""
    base = Path(__file__).resolve().parent
    room = Classroom(key="demo", course_cid=DEMO_CID, label="Demo class",
                     roster_csv=DEMO_ROSTER, delta_t_max_seconds=15)
    return Config(
        pc_secret=secrets.token_bytes(32),
        app_secret=secrets.token_bytes(32),
        session=room,
        delta_t_max_seconds=room.delta_t_max_seconds,
        submit_window_seconds=room.submit_window_seconds,
        capture_window_seconds=room.capture_window_seconds,
        clock_skew_tolerance_seconds=room.clock_skew_tolerance_seconds,
        roster_csv=room.roster_csv,
        base_dir=base,
    )


def load_config(forced_demo: bool) -> tuple[Config, bool]:
    if forced_demo:
        return demo_config(), True
    try:
        return config_mod.load(), False
    except config_mod.ConfigError:
        return demo_config(), True


def main() -> int:
    cfg, is_demo = load_config("--demo" in sys.argv)
    if is_demo:
        print("no class configured yet -- running the built-in demo.")
        print("Run `python3 -m server.admin` to set up your own.\n")
    roster = roster_mod.load(cfg.roster_path, cfg.session.course_cid)
    verifier = Verifier(cfg, roster)
    now = int(time.time())

    source_text, gen_t = make_source_qr(cfg.pc_secret, cfg.session.c_id, gen_t=now)
    print(f"session      {cfg.session.display}")
    print(f"C_ID         0x{cfg.session.c_id:08x}")
    print(f"roster       {len(roster)} students from {roster.source}")
    print(f"source QR    {source_text}  ({len(source_text)} chars)\n")

    if not roster.students:
        print("[-] no students on this roster -- check the CSV and the class CID.")
        return 1
    good = next((s for s in roster.students if not roster.is_muop(s)), roster.students[0])
    # A roster need not contain a MUoP case; fall back to any student so the
    # other five verdicts still run.
    flagged = next((s for s in roster.students if roster.is_muop(s)), None)

    other_source, _ = make_source_qr(cfg.pc_secret, derive_cid("IEC-2026-LAB", "2026-01-01", "A"), gen_t=now)

    cases = [
        ("① pass",           make_response_qr(cfg.app_secret, source_text, good.uuid, now + 3, now + 3)),
        ("② user not found", make_response_qr(cfg.app_secret, source_text, UNREGISTERED, now + 3, now + 3)),
        ("③ MUoP",           make_response_qr(cfg.app_secret, source_text,
                                              (flagged or good).uuid, now + 3, now + 3)),
        ("④ timeout",        make_response_qr(cfg.app_secret, source_text, good.uuid,
                                              now + cfg.delta_t_max_seconds + 30,
                                              now + cfg.delta_t_max_seconds + 30)),
        ("⑥ wrong session",  make_response_qr(cfg.app_secret, other_source, good.uuid, now + 3, now + 3)),
        ("⑦ garbage",        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
    ]

    signed = next((s for s in roster.students if s.pubkey), None)
    if signed is not None:
        cases.insert(4, ("⑤ device mismatch",
                         make_response_qr(cfg.app_secret, source_text, signed.uuid, now + 3, now + 3)))
    if flagged is None:
        print("note: no student on this roster has two devices, so ③ cannot fire here.\n")
    # Pin the station's clock just after the scenario, so the reported ages are
    # the ones the scenario describes rather than whatever the wall clock says.
    station_now = now + 4
    width = max(len(name) for name, _ in cases)
    for name, payload in cases:
        result = verifier.verify(payload, now=station_now)
        print(f"{name.ljust(width)}  {result.line()}")

    print(f"\nresponse QR  {len(cases[0][1])} chars")

    if "--png" in sys.argv:
        print()
        _write_png(source_text, cfg.path("data/sim_source.png"))
        _write_png(cases[0][1], cfg.path("data/sim_response.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
