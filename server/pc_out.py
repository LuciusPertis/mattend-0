"""PC (out) standalone: just the projected QR, nothing else.

Use this when the projector and the scanner are different machines. If they are
the same machine, run `python3 -m server.app` instead -- it shows this QR in its
left half.

    python3 -m server.pc_out              maximised, still minimisable
    python3 -m server.pc_out --fullscreen true fullscreen, no decorations
"""

from __future__ import annotations

import sys
import tkinter as tk

from . import config as config_mod
from . import ui
from .protocol import make_source_qr


class SourceDisplay:
    def __init__(self, cfg, fullscreen: bool = False):
        self.cfg = cfg
        self.c_id = cfg.session.c_id
        self.gen_t = 0
        self.remaining = 0

        self.root = tk.Tk()
        self.root.title("mattend - source QR")
        self.root.configure(bg=ui.BG)
        self.root.geometry("760x900")
        if fullscreen:
            self.root.attributes("-fullscreen", True)
        else:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                pass
        self.root.bind("<Escape>", lambda _e: self.root.attributes("-fullscreen", False))
        self.root.bind("q", lambda _e: self.root.destroy())
        self.root.bind("<F11>", self._toggle_fullscreen)

        self.panel = ui.QRPanel(self.root, caption=cfg.session.display)
        self.panel.pack(expand=True, fill="both")
        self.panel.subheading.config(text=f"C_ID 0x{self.c_id:08x}")

        self.rotate()

    def _toggle_fullscreen(self, _event=None):
        self.root.attributes("-fullscreen", not self.root.attributes("-fullscreen"))

    def rotate(self):
        text, self.gen_t = make_source_qr(self.cfg.pc_secret, self.c_id)
        self.panel.show(text)
        self.remaining = self.cfg.qr_rotate_seconds
        self.tick()

    def tick(self):
        self.panel.footer.config(
            text=f"v{self.panel.version} · Gen_T {self.gen_t} · refresh {self.remaining}s"
            f" · scan within {self.cfg.delta_t_max_seconds}s"
        )
        if self.remaining <= 0:
            self.root.after(0, self.rotate)
            return
        self.remaining -= 1
        self.root.after(1000, self.tick)

    def run(self):
        self.root.mainloop()


def main() -> int:
    try:
        cfg = config_mod.load()
    except config_mod.ConfigError as exc:
        print(f"[-] {exc}", file=sys.stderr)
        return 2
    print(f"[+] PC (out) · {cfg.session.display} · C_ID 0x{cfg.session.c_id:08x}")
    print("[+] q quits, F11 toggles fullscreen.")
    SourceDisplay(cfg, fullscreen="--fullscreen" in sys.argv).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
