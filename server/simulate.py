"""End-to-end dry run with no camera and no phone.

Walks a payload through both hops in software and prints the verdict for each
of the six outcomes, so the pipeline can be checked before anyone stands in
front of a webcam.

    python -m server.simulate               all six cases
    python -m server.simulate --png         also write QR pngs to data/
"""

from __future__ import annotations

import sys
import time

from . import config as config_mod
from . import roster as roster_mod
from . import verify as verify_mod
from .config import derive_cid
from .protocol import make_response_qr, make_source_qr
from .verify import Verifier

UNREGISTERED = "99999999-9999-4999-8999-999999999999"


def _write_png(text: str, path) -> None:
    import qrcode

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(path)
    print(f"    wrote {path} (QR v{qr.version}, {len(text)} chars)")


def main() -> int:
    cfg = config_mod.load()
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
        ("① pass",           make_response_qr(cfg.app_secret, source_text, good.uuid, now + 3)),
        ("② user not found", make_response_qr(cfg.app_secret, source_text, UNREGISTERED, now + 3)),
        ("③ MUoP",           make_response_qr(cfg.app_secret, source_text,
                                              (flagged or good).uuid, now + 3)),
        ("④ timeout",        make_response_qr(cfg.app_secret, source_text, good.uuid, now + cfg.delta_t_max_seconds + 30)),
        ("⑤ wrong session",  make_response_qr(cfg.app_secret, other_source, good.uuid, now + 3)),
        ("⑥ garbage",        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
    ]

    if flagged is None:
        print("note: no student on this roster has two devices, so ③ cannot fire here.\n")
    width = max(len(name) for name, _ in cases)
    for name, payload in cases:
        result = verifier.verify(payload)
        print(f"{name.ljust(width)}  {result.line()}")

    print(f"\nresponse QR  {len(cases[0][1])} chars")

    if "--png" in sys.argv:
        print()
        _write_png(source_text, cfg.path("data/sim_source.png"))
        _write_png(cases[0][1], cfg.path("data/sim_response.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
