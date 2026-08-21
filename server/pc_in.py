"""PC (in) headless: scan, verify, log to the terminal. No window.

The GUI lives in `python3 -m server.app` now. This stays for the two-machine
setup where the scanner has no display worth using, and for debugging -- it
prints one line per scan and nothing else.

    python3 -m server.pc_in
    python3 -m server.pc_in --list-cameras
"""

from __future__ import annotations

import sys
import time

from . import camera as camera_mod
from . import config as config_mod
from . import roster as roster_mod
from . import verify as verify_mod
from .store import Store
from .verify import Verifier


def main() -> int:
    if "--list-cameras" in sys.argv:
        found = camera_mod.list_cameras()
        for index, width, height in found:
            print(f"  camera_index {index}  ->  {width}x{height}")
        if not found:
            print("  no cameras found. Check the webcam is plugged in and not held by another app.")
        return 0

    try:
        cfg = config_mod.load()
        roster = roster_mod.load(cfg.roster_path, cfg.session.course_cid)
    except (config_mod.ConfigError, roster_mod.RosterError) as exc:
        print(f"[-] {exc}", file=sys.stderr)
        return 2

    verifier = Verifier(cfg, roster)
    store = Store(cfg.db_file)
    worker = camera_mod.CameraWorker(cfg.camera_index)
    if not worker.start():
        print(f"[-] {worker.error}. Try --list-cameras.", file=sys.stderr)
        return 2

    print(f"[+] PC (in) · {cfg.session.display} · C_ID 0x{cfg.session.c_id:08x}")
    print(f"[+] roster {len(roster)} students from {roster.source}")
    print("[+] Ctrl-C to stop.\n")

    seen: set[str] = set()
    present = flagged = 0

    try:
        while True:
            try:
                payload = worker.payloads.get(timeout=0.5)
            except Exception:
                if worker.error:
                    print(f"[-] {worker.error}", file=sys.stderr)
                    break
                continue

            result = verifier.verify(payload)
            key = result.relay.device_uuid or f"bad:{hash(payload) & 0xFFFFFF}"
            if key in seen:
                continue
            seen.add(key)

            print(f"  {result.line()}", flush=True)
            if result.recordable:
                store.record(result)
                if result.verdict == verify_mod.OK:
                    present += 1
                else:
                    flagged += 1
    except KeyboardInterrupt:
        print()
    finally:
        worker.stop()
        out = store.export_csv(cfg.path("data/attendance_export.csv"), cfg.session.course_cid)
        print(f"[+] present {present} · flagged {flagged} · dump {store.path}")
        print(f"[+] csv {out}")
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
