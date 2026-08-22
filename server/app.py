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
import time
import traceback
import tkinter as tk

from . import camera as camera_mod
from . import keys as keys_mod
from . import ledger as ledger_mod
from . import config as config_mod
from . import roster as roster_mod
from . import ui
from . import verify as verify_mod
from .protocol import make_source_qr
from .store import Store
from .verify import Verifier

POLL_MS = 60
PREVIEW_HEIGHT = 132
MIRROR_PREVIEW = True   # front-facing camera: the preview should read as a mirror


class OperatorApp:
    def __init__(self, cfg, roster):
        self.cfg = cfg
        self.roster = roster
        # A fresh P_c every launch, in memory only. Rotating invalidates every
        # source QR already sitting on a phone -- see keys.KeyRing.
        self.pc_keys = (keys_mod.KeyRing.ephemeral() if cfg.rotate_key_on_launch
                        else keys_mod.KeyRing.fixed(cfg.pc_secret))
        self.verifier = Verifier(cfg, roster, pc_keys=self.pc_keys)
        self.store = Store(cfg.db_file)
        self.c_id = cfg.session.c_id
        self.gen_t = 0
        self.remaining = 0
        self.ledger = ledger_mod.ScanLedger()
        self._preview_image = None  # keep a reference or Tk garbage-collects it
        self._timers: dict[str, str] = {}
        self._alive = True
        self.last_result = None
        self.started = time.monotonic()

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
        self.root.bind("k", lambda _e: self.rotate_key())

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

        # Bottom strip: telemetry fills the width, camera sits in the corner.
        strip = tk.Frame(left, bg=ui.BG)
        strip.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 14))
        strip.columnconfigure(0, weight=1)

        self.metrics = ui.MetricsPanel(strip)
        self.metrics.grid(row=0, column=0, sticky="nw")

        self.preview = tk.Label(strip, bg="#000000", fg=ui.DIM, font=("TkFixedFont", 9),
                                width=24, height=8)
        self.preview.grid(row=0, column=1, sticky="se", padx=(16, 0))

    def _build_right(self):
        right = tk.Frame(self.root, bg=ui.PANEL_BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        header = tk.Frame(right, bg=ui.PANEL_BG)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        tk.Label(header, text="VERIFICATIONS", bg=ui.PANEL_BG, fg=ui.FG,
                 font=("TkDefaultFont", 13, "bold")).pack(side="left")
        self.counts = tk.Label(header, text="", bg=ui.PANEL_BG, fg=ui.DIM, font=("TkFixedFont", 11))
        self.counts.pack(side="right")

        self.banner = ui.TransientBanner(right)
        self.banner.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.banner.grid_remove()

        self.queue = ui.VerdictQueue(right)
        self.queue.grid(row=2, column=0, sticky="nsew")

    def _build_status(self):
        self.status = tk.Label(
            self.root, text="", bg=ui.BG, fg=ui.DIM, font=("TkFixedFont", 10), anchor="w",
        )
        self.status.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))

    # ---------- QR rotation ----------

    def rotate(self):
        text, self.gen_t = make_source_qr(self.pc_keys.current, self.c_id)
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

    def rotate_key(self):
        """Burn the current P_c. Every source QR already on a phone dies now."""
        generation = self.pc_keys.rotate()
        self.rotate()                       # repaint with the new key immediately
        self.banner.show(
            "KEY ROTATED",
            f"generation {generation} — every QR taken before now is void",
            "#d8a020",
        )
        print(f"[!] P_c rotated -> generation {generation}", flush=True)
        self.refresh_metrics()

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
        outcome = self.ledger.submit(key, result)

        if outcome == ledger_mod.SILENT:
            return                      # same phone still in frame

        self.last_result = result

        if outcome == ledger_mod.REPEAT:
            # Already passed. Keep the original verdict, but make the rescan
            # visible instead of dropping it on the floor.
            prior = self.ledger.record_for(key)
            count = self.ledger.count(key)
            self.banner.show(
                f"{prior.title} · ALREADY MARKED",
                f"{prior.subtitle or 'present'} — scanned {count}x, no need to scan again",
                ui.hex_colour(prior.color),
            )
            self.store.record(prior)    # bumps scan_count in the dump
            print(f"  REPEAT        | {prior.title} | already marked present (scan {count})", flush=True)
        else:
            self.queue.push(result)
            print(f"  {result.line()}", flush=True)
            if result.recordable:
                self.store.record(result)

        self.counts.config(
            text=f"present {self.ledger.present}   flagged {self.ledger.flagged}   roster {len(self.roster)}"
        )
        self.refresh_metrics()

    def refresh_preview(self):
        frame = self.camera.latest_frame() if self.camera_ok else None
        if frame is not None:
            data = camera_mod.preview_ppm(frame, PREVIEW_HEIGHT, mirror=MIRROR_PREVIEW)
            if data:
                self._preview_image = tk.PhotoImage(data=data)
                self.preview.config(image=self._preview_image, text="", width=0, height=0)
        elif not self.camera_ok:
            self.preview.config(image="", text=self.camera.error or "no camera")

        self.refresh_metrics()
        self._schedule("preview", 200, self.refresh_preview)

    def refresh_metrics(self):
        cam = self.camera
        warn = "#d8a020"
        bad = "#c94a4a"

        width, height = cam.actual_size
        fps = cam.fps
        self.metrics.set(
            "CAMERA",
            f"idx {self.cfg.camera_index}  {width}x{height}  {fps:5.1f} fps  "
            f"frames {cam.frames_seen}  drops {cam.drops}",
            bad if (cam.error or not self.camera_ok) else (warn if fps and fps < 5 else None),
        )

        since = cam.seconds_since_decode
        self.metrics.set(
            "DECODE",
            f"qr {cam.decodes}  " + ("last never" if since is None else f"last {since:5.1f}s ago")
            + f"  pending {cam.payloads.qsize()}",
        )
        self.metrics.set(
            "SOURCE",
            f"v{self.qr.version}  C_ID 0x{self.c_id:08x}  Gen_T {self.gen_t}  rotate in {self.remaining}s",
        )
        self.metrics.set(
            "WINDOW",
            f"dT {self.cfg.delta_t_max_seconds}s  submit {self.cfg.submit_window_seconds}s"
            f"  journey {self.cfg.capture_window_seconds}s  skew {self.cfg.clock_skew_tolerance_seconds}s",
        )
        mode = "rotating" if self.cfg.rotate_key_on_launch else "fixed (config.json)"
        signed = self.roster.signed_devices
        total = len(self.roster)
        self.metrics.set(
            "KEYS",
            f"P_c {mode} gen {self.pc_keys.generation}  age {int(self.pc_keys.age_seconds)}s"
            f"   devices signed {signed}/{total}"
            + ("  REQUIRED" if self.cfg.require_signature else ""),
            warn if (self.cfg.require_signature and signed < total) else None,
        )
        rejected = len(getattr(self.roster, "rejected", []))
        self.metrics.set(
            "ROSTER",
            f"{self.cfg.session.course_cid}  {len(self.roster)} students  "
            f"{len(self.roster.muop_report())} muop  {rejected} rejected",
            warn if rejected else None,
        )
        self.metrics.set(
            "TALLY",
            f"present {self.ledger.present}  flagged {self.ledger.flagged}  "
            f"devices {self.ledger.devices}  repeats {self.ledger.repeats}  "
            f"cards {len(self.queue.results)}",
        )
        if self.last_result is None:
            self.metrics.set("LAST", "nothing scanned yet")
        else:
            result = self.last_result
            detail = f"  dT {result.relay.delta_t}s" if result.recordable else ""
            self.metrics.set(
                "LAST",
                f"{result.verdict}  {result.title}{detail}",
                ui.hex_colour(result.color),
            )
        elapsed = int(time.monotonic() - self.started)
        self.metrics.set("UPTIME", f"{elapsed // 3600:02d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}")

        note = cam.error or "running"
        self.status.config(text=f"{note}   |   q quit · e export · r reload roster · c clear · F11 fullscreen")

    # ---------- actions ----------

    def export(self):
        out = self.store.export_csv(self.cfg.path("data/attendance_export.csv"), self.cfg.session.course_cid)
        print(f"[+] exported {out}", flush=True)

    def reload_roster(self):
        try:
            self.roster = roster_mod.load(self.cfg.roster_path, self.cfg.session.course_cid)
            self.verifier.roster = self.roster
            self.ledger.clear()         # let a newly-registered student scan again
            print(f"[+] roster reloaded: {len(self.roster)} students", flush=True)
        except roster_mod.RosterError as exc:
            print(f"[-] roster reload failed: {exc}", file=sys.stderr, flush=True)

    def _toggle_fullscreen(self, _event=None):
        self.root.attributes("-fullscreen", not self.root.attributes("-fullscreen"))

    def quit(self):
        if not self._alive:
            return
        self._alive = False
        self.banner.cancel()
        for timer in self._timers.values():
            try:
                self.root.after_cancel(timer)
            except tk.TclError:
                pass
        self.camera.stop()
        self.export()
        print(
            f"[+] present {self.ledger.present} · flagged {self.ledger.flagged}"
            f" · repeats {self.ledger.repeats} · dump {self.store.path}", flush=True
        )
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
    print(f"[+] roster {len(roster)} students from {roster.source}"
          f" ({roster.signed_devices} with device keys)")
    if cfg.rotate_key_on_launch:
        print("[+] P_c is fresh this launch and never written to disk; press k to rotate again.")
    if cfg.require_signature and roster.signed_devices < len(roster):
        print(f"[!] require_signature is on but {len(roster) - roster.signed_devices} student(s)"
              " have no device key -- they will be rejected until they re-enrol.")
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
