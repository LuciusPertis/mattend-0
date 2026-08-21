"""PC (in): webcam scanner, verifier and verdict wall.

detectAndDecodeMulti reads several phones out of one frame, so a queue of
students can hold their screens up together. Each decoded payload runs through
the verifier; the outcome tints the box drawn around that QR and pushes a card
onto the panel on the right -- the four states from the diagram, plus two for
payloads that never resolve to an identity at all.

    python -m server.pc_in
    python -m server.pc_in --list-cameras
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections import OrderedDict

import cv2

from . import config as config_mod
from . import roster as roster_mod
from . import verify as verify_mod
from .store import Store
from .verify import Verifier

FONT = cv2.FONT_HERSHEY_SIMPLEX
PANEL_WIDTH = 380
CARD_HEIGHT = 88
CARD_GAP = 10


def _bgr(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = rgb
    return (b, g, r)


class VerdictWall:
    """Most recent verdict per device, newest first, expiring after a TTL."""

    def __init__(self, ttl: int, capacity: int = 6):
        self.ttl = ttl
        self.capacity = capacity
        self.cards: OrderedDict[str, tuple[float, verify_mod.Result]] = OrderedDict()

    def push(self, key: str, result) -> None:
        self.cards.pop(key, None)
        self.cards[key] = (time.time(), result)
        while len(self.cards) > self.capacity:
            self.cards.popitem(last=False)

    def live(self) -> list:
        now = time.time()
        fresh = [(k, v) for k, v in self.cards.items() if now - v[0] <= self.ttl]
        self.cards = OrderedDict(fresh)
        return [result for _stamp, result in reversed([v for _k, v in fresh])]


def draw_card(canvas, result, x: int, y: int, width: int) -> None:
    color = _bgr(result.color)
    cv2.rectangle(canvas, (x, y), (x + width, y + CARD_HEIGHT), (255, 255, 255), -1)
    cv2.rectangle(canvas, (x, y), (x + width, y + CARD_HEIGHT), color, 2)
    cv2.rectangle(canvas, (x, y), (x + 8, y + CARD_HEIGHT), color, -1)

    cv2.putText(canvas, result.title[:22], (x + 20, y + 30), FONT, 0.72, (25, 25, 25), 2, cv2.LINE_AA)
    if result.subtitle:
        cv2.putText(canvas, result.subtitle[:30], (x + 20, y + 56), FONT, 0.55, (90, 90, 90), 1, cv2.LINE_AA)
    cv2.putText(canvas, result.badge[:30], (x + 20, y + 78), FONT, 0.52, color, 2, cv2.LINE_AA)


def compose(frame, results, cfg, roster, stats):
    height, width = frame.shape[:2]
    canvas = cv2.copyMakeBorder(frame, 0, 0, 0, PANEL_WIDTH, cv2.BORDER_CONSTANT, value=(28, 28, 28))

    x = width + 16
    cv2.putText(canvas, cfg.session.course_cid[:24], (x, 34), FONT, 0.7, (240, 240, 240), 2, cv2.LINE_AA)
    cv2.putText(
        canvas, f"{cfg.session.date}  slot {cfg.session.slot}", (x, 58), FONT, 0.48, (150, 150, 150), 1, cv2.LINE_AA
    )
    cv2.putText(
        canvas,
        f"roster {len(roster)}   present {stats['present']}   flagged {stats['flagged']}",
        (x, 82), FONT, 0.46, (150, 150, 150), 1, cv2.LINE_AA,
    )
    cv2.line(canvas, (x, 96), (width + PANEL_WIDTH - 16, 96), (70, 70, 70), 1)

    y = 112
    for result in results:
        if y + CARD_HEIGHT > height - 40:
            break
        draw_card(canvas, result, x, y, PANEL_WIDTH - 32)
        y += CARD_HEIGHT + CARD_GAP

    cv2.putText(
        canvas, "q quit   e export csv   r reload roster",
        (x, height - 16), FONT, 0.44, (120, 120, 120), 1, cv2.LINE_AA,
    )
    return canvas


@contextlib.contextmanager
def _muted_stderr():
    """Silence the native layer, which does not go through Python's sys.stderr."""
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


def list_cameras(limit: int = 6) -> None:
    """Probe the first few indices. Probing an index with no camera behind it is
    normal, but OpenCV's V4L backend complains loudly on stderr each time and
    scrolls the useful lines away -- so mute it for the duration of the probe."""
    found = 0
    for index in range(limit):
        with _muted_stderr():
            cap = cv2.VideoCapture(index)
            opened = cap.isOpened()
            ok, frame = cap.read() if opened else (False, None)
            cap.release()
        if opened and ok and frame is not None:
            height, width = frame.shape[:2]
            print(f"  camera_index {index}  ->  {width}x{height}")
            found += 1
    if not found:
        print("  no cameras found. Check the webcam is plugged in and not held by another app.")


def main() -> int:
    if "--list-cameras" in sys.argv:
        list_cameras()
        return 0

    try:
        cfg = config_mod.load()
        roster = roster_mod.load(cfg.roster_path, cfg.session.course_cid)
    except (config_mod.ConfigError, roster_mod.RosterError) as exc:
        print(f"[-] {exc}", file=sys.stderr)
        return 2

    verifier = Verifier(cfg, roster)
    store = Store(cfg.db_file)
    wall = VerdictWall(cfg.card_ttl_seconds)
    detector = cv2.QRCodeDetector()

    cap = cv2.VideoCapture(cfg.camera_index)
    if not cap.isOpened():
        print(f"[-] cannot open camera {cfg.camera_index}. Try --list-cameras.", file=sys.stderr)
        return 2

    print(f"[+] PC (in) · {cfg.session.display} · C_ID 0x{cfg.session.c_id:08x}")
    print(f"[+] roster {len(roster)} students from {roster.source}")
    muop = roster.muop_report()
    if muop:
        print(f"[!] {len(muop)} email(s) registered on multiple devices -- these will report MUoP:")
        for email, uuids in muop.items():
            print(f"    {email}: {len(uuids)} devices")
    print("[+] q to quit, e to export CSV, r to reload the roster.\n")

    seen_this_run: set[str] = set()
    stats = {"present": 0, "flagged": 0}

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[-] dropped frame", file=sys.stderr)
                break

            found, decoded, points, _ = detector.detectAndDecodeMulti(frame)
            if found and decoded is not None:
                for index, text in enumerate(decoded):
                    if not text:
                        continue
                    result = verifier.verify(text)

                    if points is not None and index < len(points):
                        box = points[index].astype(int)
                        cv2.polylines(frame, [box], True, _bgr(result.color), 3, cv2.LINE_AA)

                    key = result.relay.device_uuid or f"bad:{hash(text) & 0xFFFF}"
                    wall.push(key, result)

                    if key in seen_this_run:
                        continue
                    seen_this_run.add(key)
                    print(f"  {result.line()}")
                    if result.recordable:
                        store.record(result)
                        if result.verdict == verify_mod.OK:
                            stats["present"] += 1
                        else:
                            stats["flagged"] += 1

            cv2.imshow("mattend · PC (in)", compose(frame, wall.live(), cfg, roster, stats))

            key_press = cv2.waitKey(1) & 0xFF
            if key_press == ord("q"):
                break
            if key_press == ord("e"):
                out = store.export_csv(cfg.path("data/attendance_export.csv"), cfg.session.course_cid)
                print(f"[+] exported to {out}")
            if key_press == ord("r"):
                try:
                    roster = roster_mod.load(cfg.roster_path, cfg.session.course_cid)
                    verifier.roster = roster
                    print(f"[+] roster reloaded: {len(roster)} students")
                except roster_mod.RosterError as exc:
                    print(f"[-] roster reload failed: {exc}", file=sys.stderr)
    finally:
        cap.release()
        cv2.destroyAllWindows()
        out = store.export_csv(cfg.path("data/attendance_export.csv"), cfg.session.course_cid)
        print(f"\n[+] present {stats['present']} · flagged {stats['flagged']} · dump {store.path}")
        print(f"[+] csv {out}")
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
