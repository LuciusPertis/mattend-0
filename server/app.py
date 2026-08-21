"""The operator GUI: one window, both halves of the relay.

    ┌───────────────────────┬──────────────────────┐
    │   QR #1, refreshing   │  newest 4 verdicts   │
    │   (what the phone     │  large, older ones    │
    │    scans)             │  shrink and queue     │
    │   [aim preview]       │  off the bottom       │
    └───────────────────────┴──────────────────────┘

Starts maximised rather than true-fullscreen, so it still minimises like a
normal window. F11 toggles real fullscreen.

    python3 -m server.app
    python3 -m server.app --list-cameras
"""

from __future__ import annotations

import queue as queue_mod
import sys
import traceback
import tkinter as tk

from . import camera as camera_mod
from . import config as config_mod
from . import roster as roster_mod
from . import ui
from . import verify as verify_mod
from .protocol import make_source_qr
from .store import Store
from .verify import Verifier

POLL_MS = 60
PREVIEW_HEIGHT = 132


class OperatorApp:
    def __init__(self, cfg, roster):
        self.cfg = cfg
        self.roster = roster
        self.verifier = Verifier(cfg, roster)
        self.store = Store(cfg.db_file)
        self.c_id = cfg.session.c_id
        self.gen_t = 0
        self.remaining = 0
        self.seen: set[str] = set()
        self.present = 0
        self.flagged = 0
        self._preview_image = None  # keep a reference or Tk garbage-collects it
        self._timers: dict[str, str] = {}
        self._alive = True

        self.root = tk.Tk()
        self.root.title("mattend - attendance station")
        self.root.configure(bg=ui.BG)
        self.root.geometry("1400x860")
        self._maximise()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.bind("<F11>", self._toggle_fullscreen)
        self.root.bind("<Escape>", lambda _e: self.root.attributes("-fullscreen", False))
        self.root.bind("q", lambda _e: self.quit())
        self.root.bind("e", lambda _e: self.export())
        self.root.bind("r", lambda _e: self.reload_roster())
        self.root.bind("c", lambda _e: self.queue.clear())

        self.root.columnconfigure(0, weight=1, uniform="half")
        self.root.columnconfigure(1, weight=1, uniform="half")
        self.root.rowconfigure(0, weight=1)

        self._build_left()
        self._build_right()
        self._build_status()

        self.camera = camera_mod.CameraWorker(cfg.camera_index)
        self.camera_ok = self.camera.start()

        self.rotate()
        self._schedule("drain", POLL_MS, self.drain)
        self._schedule("preview", 200, self.refresh_preview)

    def _schedule(self, name: str, delay: int, callback) -> None:
        """Repeating timers are tracked so quit() can cancel them. Otherwise a
        pending `after` fires against a destroyed widget and Tk complains."""
        if self._alive:
            self._timers[name] = self.root.after(delay, callback)

    # ---------- layout ----------

    def _maximise(self):
        # -zoomed keeps window decorations (so it minimises); -fullscreen does not.
        try:
            self.root.attributes("-zoomed", True)
        except tk.TclError:
            self.root.state("normal")
            self.root.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")

    def _build_left(self):
        left = tk.Frame(self.root, bg=ui.BG)
        left.grid(row=0, column=0, sticky="nsew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.qr = ui.QRPanel(left, caption=self.cfg.session.display)
        self.qr.grid(row=0, column=0, sticky="nsew")
        self.qr.subheading.config(text=f"C_ID 0x{self.c_id:08x}")

        self.preview = tk.Label(left, bg=ui.BG, fg=ui.DIM, font=("TkFixedFont", 10))
        self.preview.grid(row=1, column=0, pady=(0, 12))

    def _build_right(self):
        right = tk.Frame(self.root, bg=ui.PANEL_BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        header = tk.Frame(right, bg=ui.PANEL_BG)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        tk.Label(header, text="VERIFICATIONS", bg=ui.PANEL_BG, fg=ui.FG,
                 font=("TkDefaultFont", 13, "bold")).pack(side="left")
        self.counts = tk.Label(header, text="", bg=ui.PANEL_BG, fg=ui.DIM, font=("TkFixedFont", 11))
        self.counts.pack(side="right")

        self.queue = ui.VerdictQueue(right)
        self.queue.grid(row=1, column=0, sticky="nsew")

    def _build_status(self):
        self.status = tk.Label(
            self.root, text="", bg=ui.BG, fg=ui.DIM, font=("TkFixedFont", 10), anchor="w",
        )
        self.status.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))

    # ---------- QR rotation ----------

    def rotate(self):
        text, self.gen_t = make_source_qr(self.cfg.pc_secret, self.c_id)
        self.qr.show(text)
        self.remaining = self.cfg.qr_rotate_seconds
        self.tick()

    def tick(self):
        self.qr.footer.config(
            text=f"v{self.qr.version} · refresh {self.remaining}s · scan within {self.cfg.delta_t_max_seconds}s"
        )
        if self.remaining <= 0:
            self._schedule("tick", 0, self.rotate)
            return
        self.remaining -= 1
        self._schedule("tick", 1000, self.tick)

    # ---------- scanning ----------

    def drain(self):
        while True:
            try:
                payload = self.camera.payloads.get_nowait()
            except queue_mod.Empty:
                break
            try:
                self.handle(payload)
            except Exception:  # one bad payload must not kill the GUI loop
                traceback.print_exc()
        self._schedule("drain", POLL_MS, self.drain)

    def handle(self, payload: str):
        result = self.verifier.verify(payload)
        key = result.relay.device_uuid or f"bad:{hash(payload) & 0xFFFFFF}"
        if key in self.seen:
            return                      # same phone still in frame; don't re-card it
        self.seen.add(key)

        self.queue.push(result)
        print(f"  {result.line()}", flush=True)
        if result.recordable:
            self.store.record(result)
            if result.verdict == verify_mod.OK:
                self.present += 1
            else:
                self.flagged += 1
        self.counts.config(text=f"present {self.present}   flagged {self.flagged}   roster {len(self.roster)}")

    def refresh_preview(self):
        frame = self.camera.latest_frame() if self.camera_ok else None
        if frame is not None:
            import cv2

            height, width = frame.shape[:2]
            scale = PREVIEW_HEIGHT / height
            small = cv2.resize(frame, (int(width * scale), PREVIEW_HEIGHT))
            ok, buf = cv2.imencode(".ppm", small)
            if ok:
                # Tk reads PPM natively, so no PIL round-trip is needed here.
                self._preview_image = tk.PhotoImage(data=buf.tobytes())
                self.preview.config(image=self._preview_image, text="")
        elif not self.camera_ok:
            self.preview.config(image="", text=self.camera.error or "no camera")

        note = self.camera.error or f"camera {self.cfg.camera_index} · {self.camera.frames_seen} frames"
        self.status.config(text=f"{note}    |    q quit · e export · r reload roster · c clear · F11 fullscreen")
        self._schedule("preview", 200, self.refresh_preview)

    # ---------- actions ----------

    def export(self):
        out = self.store.export_csv(self.cfg.path("data/attendance_export.csv"), self.cfg.session.course_cid)
        print(f"[+] exported {out}", flush=True)

    def reload_roster(self):
        try:
            self.roster = roster_mod.load(self.cfg.roster_path, self.cfg.session.course_cid)
            self.verifier.roster = self.roster
            self.seen.clear()           # let a newly-registered student scan again
            print(f"[+] roster reloaded: {len(self.roster)} students", flush=True)
        except roster_mod.RosterError as exc:
            print(f"[-] roster reload failed: {exc}", file=sys.stderr, flush=True)

    def _toggle_fullscreen(self, _event=None):
        self.root.attributes("-fullscreen", not self.root.attributes("-fullscreen"))

    def quit(self):
        if not self._alive:
            return
        self._alive = False
        for timer in self._timers.values():
            try:
                self.root.after_cancel(timer)
            except tk.TclError:
                pass
        self.camera.stop()
        self.export()
        print(f"[+] present {self.present} · flagged {self.flagged} · dump {self.store.path}", flush=True)
        self.store.close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


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

    print(f"[+] {cfg.session.display} · C_ID 0x{cfg.session.c_id:08x}")
    print(f"[+] roster {len(roster)} students from {roster.source}")
    muop = roster.muop_report()
    if muop:
        print(f"[!] {len(muop)} email(s) on multiple devices -- these report MUoP:")
        for email, uuids in muop.items():
            print(f"    {email}: {len(uuids)} devices")

    app = OperatorApp(cfg, roster)
    if not app.camera_ok:
        print(f"[-] {app.camera.error}. Try --list-cameras.", file=sys.stderr)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
