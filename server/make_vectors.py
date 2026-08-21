"""Emit known-answer vectors so client_web/selftest.html can prove the JS half
of P_s agrees with this Python byte for byte.

    python -m server.make_vectors
"""

from __future__ import annotations

import json
from pathlib import Path

from . import codec, config as config_mod
from .protocol import make_response_qr, make_source_qr

OUT = Path(__file__).resolve().parent.parent / "client_web" / "vectors.json"

UUIDS = [
    "11111111-1111-4111-8111-111111111111",
    "0f8fad5b-d9cb-469f-a165-70867728950e",
    "ffffffff-ffff-4fff-bfff-ffffffffffff",
]


def main() -> int:
    cfg = config_mod.load()

    b32 = [
        {"bytes": list(raw), "text": codec.b32encode(bytes(raw))}
        for raw in (b"", b"\x00", b"\xff" * 5, bytes(range(15)), bytes((i * 37) % 256 for i in range(42)))
    ]

    fuse = []
    for index, device_uuid in enumerate(UUIDS):
        gen_t = 1_700_000_000 + index * 911
        source, _ = make_source_qr(cfg.pc_secret, 0xD801CBFE ^ index, gen_t=gen_t)
        cap_t = gen_t + 7 + index
        fuse.append(
            {
                "source": source,
                "uuid": device_uuid,
                "capT": cap_t,
                "expected": make_response_qr(cfg.app_secret, source, device_uuid, cap_t),
            }
        )

    OUT.write_text(json.dumps({"b32": b32, "fuse": fuse}, indent=2) + "\n")
    print(f"[+] wrote {len(b32)} base32 and {len(fuse)} fusion vectors to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
