"""PC (out): the projected QR.

A fullscreen Tk window showing Z*_{CID,GT}, regenerated every
`qr_rotate_seconds` so Gen_T stays fresh. The QR is drawn straight onto a
canvas from the module matrix -- no PIL, no image files, and it rescales
cleanly to whatever the projector is.

    python -m server.pc_out            fullscreen
    python -m server.pc_out --windowed
"""

from __future__ import annotations

import sys
import tkinter as tk

import qrcode

from . import config as config_mod
from .protocol import make_source_qr

BG = "#0d0d0d"
FG = "#f4f4f4"
QUIET_MODULES = 2


class SourceDisplay:
    def __init__(self, cfg: config_mod.Config, fullscreen: bool = True):
        self.cfg = cfg
        self.c_id = cfg.session.c_id
        self.gen_t = 0
        self.remaining = 0

        self.root = tk.Tk()
        self.root.title("mattend · PC (out)")
        self.root.configure(bg=BG)
        if fullscreen:
            self.root.attributes("-fullscreen", True)
        else:
            self.root.geometry("720x820")
        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        self.root.bind("q", lambda _e: self.root.destroy())
        self.root.bind("<F11>", self._toggle_fullscreen)

        tk.Label(
            self.root, text=cfg.session.display, bg=BG, fg=FG, font=("TkDefaultFont", 22, "bold")
        ).pack(pady=(24, 4))
        tk.Label(
            self.root, text=f"C_ID 0x{self.c_id:08x}", bg=BG, fg="#7a7a7a", font=("TkFixedFont", 13)
        ).pack()

        self.canvas = tk.Canvas(self.root, bg="white", highlightthickness=0)
        self.canvas.pack(expand=True, fill="both", padx=40, pady=24)
        self.canvas.bind("<Configure>", lambda _e: self._draw())

        self.status = tk.Label(self.root, text="", bg=BG, fg="#7a7a7a", font=("TkFixedFont", 14))
        self.status.pack(pady=(0, 24))

        self._rotate()

    def _toggle_fullscreen(self, _event=None):
        self.root.attributes("-fullscreen", not self.root.attributes("-fullscreen"))

    def _rotate(self):
        text, self.gen_t = make_source_qr(self.cfg.pc_secret, self.c_id)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=QUIET_MODULES)
        qr.add_data(text)
        qr.make(fit=True)
        self.matrix = qr.get_matrix()
        self.version = qr.version
        self._draw()
        self.remaining = self.cfg.qr_rotate_seconds
        self._tick()

    def _tick(self):
        window = self.cfg.delta_t_max_seconds
        self.status.config(
            text=f"v{self.version} · Gen_T {self.gen_t} · refresh in {self.remaining}s · scan within {window}s"
        )
        if self.remaining <= 0:
            self.root.after(0, self._rotate)
            return
        self.remaining -= 1
        self.root.after(1000, self._tick)

    def _draw(self):
        matrix = getattr(self, "matrix", None)
        if not matrix:
            return
        self.canvas.delete("all")
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 2 or height < 2:
            return
        n = len(matrix)
        # Integer module size keeps every module the same width -- fractional
        # scaling is what makes projected QRs hard for a phone to lock onto.
        module = max(1, min(width, height) // n)
        side = module * n
        ox = (width - side) // 2
        oy = (height - side) // 2
        self.canvas.create_rectangle(0, 0, width, height, fill="white", outline="")
        for r, row in enumerate(matrix):
            c = 0
            while c < n:
                if not row[c]:
                    c += 1
                    continue
                start = c
                while c < n and row[c]:  # coalesce runs into one rectangle
                    c += 1
                self.canvas.create_rectangle(
                    ox + start * module, oy + r * module,
                    ox + c * module, oy + (r + 1) * module,
                    fill="black", outline="",
                )

    def run(self):
        self.root.mainloop()


def main() -> int:
    try:
        cfg = config_mod.load()
    except config_mod.ConfigError as exc:
        print(f"[-] {exc}", file=sys.stderr)
        return 2
    print(f"[+] PC (out) · {cfg.session.display} · C_ID 0x{cfg.session.c_id:08x}")
    print("[+] Esc or q to quit, F11 toggles fullscreen.")
    SourceDisplay(cfg, fullscreen="--windowed" not in sys.argv).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
