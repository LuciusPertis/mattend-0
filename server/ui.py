"""Tk widgets shared by the operator GUI and the standalone projector view.

Deliberately plain -- this is the functional pass. Colours and metrics live in
the tables at the top so restyling later is a one-file job.
"""

from __future__ import annotations

import tkinter as tk

import qrcode

BG = "#111214"
PANEL_BG = "#17181b"
CARD_BG = "#ffffff"
FG = "#f2f2f2"
DIM = "#8a8d93"
RULE = "#2a2c31"

BIG_CARDS = 4           # newest N get the large treatment
BIG_HEIGHT = 104
SMALL_HEIGHT = 40
CARD_GAP = 8
QUIET_MODULES = 2


def hex_colour(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


class QRPanel(tk.Frame):
    """Draws a QR matrix scaled to fill, with a caption underneath."""

    def __init__(self, parent, caption: str = "", **kwargs):
        super().__init__(parent, bg=BG, **kwargs)
        self.matrix: list[list[bool]] | None = None
        self.version = 0

        self.heading = tk.Label(self, text=caption, bg=BG, fg=FG, font=("TkDefaultFont", 16, "bold"))
        self.heading.pack(pady=(14, 2))
        self.subheading = tk.Label(self, text="", bg=BG, fg=DIM, font=("TkFixedFont", 11))
        self.subheading.pack()

        self.canvas = tk.Canvas(self, bg="white", highlightthickness=0)
        self.canvas.pack(expand=True, fill="both", padx=24, pady=16)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())

        self.footer = tk.Label(self, text="", bg=BG, fg=DIM, font=("TkFixedFont", 11))
        self.footer.pack(pady=(0, 14))

    def show(self, text: str) -> None:
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=QUIET_MODULES)
        qr.add_data(text)
        qr.make(fit=True)
        self.matrix = qr.get_matrix()
        self.version = qr.version
        self.redraw()

    def redraw(self) -> None:
        if not self.matrix:
            return
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 2 or height < 2:
            return
        n = len(self.matrix)
        # Integer module size only: fractional scaling smears the module grid and
        # is what makes a projected QR hard for a phone to lock onto.
        module = max(1, min(width, height) // n)
        side = module * n
        ox, oy = (width - side) // 2, (height - side) // 2
        canvas.create_rectangle(0, 0, width, height, fill="white", outline="")
        for r, row in enumerate(self.matrix):
            c = 0
            while c < n:
                if not row[c]:
                    c += 1
                    continue
                start = c
                while c < n and row[c]:  # coalesce runs of dark modules
                    c += 1
                canvas.create_rectangle(
                    ox + start * module, oy + r * module,
                    ox + c * module, oy + (r + 1) * module,
                    fill="black", outline="",
                )


class VerdictQueue(tk.Frame):
    """Newest verdict on top. The first BIG_CARDS are large; everything behind
    them shrinks to one line so more students stay visible. Cards that no longer
    fit the panel drop off the bottom."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=PANEL_BG, **kwargs)
        self.results: list = []
        self._widgets: list[tk.Widget] = []
        self._last_height = 0
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event) -> None:
        # Only a height change alters how many cards fit. Re-rendering on every
        # Configure would storm, because building the cards fires more of them.
        if abs(event.height - self._last_height) < SMALL_HEIGHT:
            return
        self._last_height = event.height
        self._render()

    def push(self, result) -> None:
        self.results.insert(0, result)
        del self.results[self._capacity():]
        self._render()

    def clear(self) -> None:
        self.results.clear()
        self._render()

    def _capacity(self) -> int:
        height = self.winfo_height()
        if height < 2:
            return BIG_CARDS + 6
        spare = height - BIG_CARDS * (BIG_HEIGHT + CARD_GAP)
        return BIG_CARDS + max(0, spare // (SMALL_HEIGHT + CARD_GAP))

    def _render(self) -> None:
        for widget in self._widgets:
            widget.destroy()
        self._widgets.clear()
        del self.results[self._capacity():]

        for index, result in enumerate(self.results):
            card = self._big(result) if index < BIG_CARDS else self._small(result)
            card.pack(fill="x", padx=10, pady=(0, CARD_GAP))
            self._widgets.append(card)

    def _big(self, result) -> tk.Frame:
        colour = hex_colour(result.color)
        outer = tk.Frame(self, bg=colour, height=BIG_HEIGHT)
        outer.pack_propagate(False)
        inner = tk.Frame(outer, bg=CARD_BG)
        inner.pack(side="right", fill="both", expand=True, padx=(8, 0))
        tk.Label(inner, text=result.title[:24], bg=CARD_BG, fg="#16181a",
                 font=("TkDefaultFont", 21, "bold"), anchor="w").pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(inner, text=(result.subtitle or "")[:38], bg=CARD_BG, fg="#5c6169",
                 font=("TkDefaultFont", 12), anchor="w").pack(fill="x", padx=16)
        tk.Label(inner, text=result.badge, bg=CARD_BG, fg=colour,
                 font=("TkDefaultFont", 11, "bold"), anchor="w").pack(fill="x", padx=16, pady=(2, 10))
        return outer

    def _small(self, result) -> tk.Frame:
        colour = hex_colour(result.color)
        outer = tk.Frame(self, bg=colour, height=SMALL_HEIGHT)
        outer.pack_propagate(False)
        inner = tk.Frame(outer, bg=CARD_BG)
        inner.pack(side="right", fill="both", expand=True, padx=(6, 0))
        tk.Label(inner, text=result.title[:20], bg=CARD_BG, fg="#16181a",
                 font=("TkDefaultFont", 12, "bold"), anchor="w").pack(side="left", padx=(14, 8))
        tk.Label(inner, text=(result.subtitle or "")[:22], bg=CARD_BG, fg="#7b8089",
                 font=("TkDefaultFont", 10), anchor="w").pack(side="left")
        tk.Label(inner, text=result.badge[:18], bg=CARD_BG, fg=colour,
                 font=("TkDefaultFont", 10, "bold"), anchor="e").pack(side="right", padx=(8, 14))
        return outer
