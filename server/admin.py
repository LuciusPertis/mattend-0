"""Class Manager: the teacher's front door.

Add the classes you teach, link each to your own Google Form, print the
enrollment QR, and launch the station. Nothing here needs a text editor.

The only fiddly input is the Google Form field ids (`entry.954365518`), which
the form editor never shows you. So this asks for the URL that Forms' own
"Get prefilled link" button produces and reads the ids straight out of it.

    python3 -m server.admin
"""

from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import qrcode

from . import config as config_mod
from . import enroll
from . import ui
from .classroom import (
    Classroom,
    ClassroomError,
    REGISTRY_NAME,
    Registry,
    guess_roles,
    migrate_from_config,
    parse_prefilled_link,
    slugify,
)

ROLES = (("uuid", "Device id (msL-key)"), ("name", "Full name"), ("cid", "Class id  (optional)"))
FIELD_BG = "#1e2024"
ENTRY_KW = dict(bg=FIELD_BG, fg=ui.FG, insertbackground=ui.FG, relief="flat",
                highlightthickness=1, highlightbackground="#31343a", highlightcolor="#4c9be8")


class ClassManager:
    def __init__(self):
        self.config_path, created = config_mod.bootstrap()
        self.base = self.config_path.parent
        self.registry = Registry.load(self.base / REGISTRY_NAME)
        migrate_from_config(self.registry, self.config_path)
        self.raw = __import__("json").loads(self.config_path.read_text())

        self.current: Classroom | None = None
        self.parsed_entries: dict[str, str] = {}
        self.role_vars: dict[str, tk.StringVar] = {}

        self.root = tk.Tk()
        self.root.title("mattend - Class Manager")
        self.root.configure(bg=ui.BG)
        self.root.geometry("1180x820")
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(1, weight=1)

        self._build_header()
        self._build_sidebar()
        self._build_editor()
        self._build_qr()

        self.refresh_list()
        if created:
            messagebox.showinfo(
                "Welcome",
                "Created your config.json with a fresh pc_secret.\n\n"
                "That key is yours: source QRs made with it verify only on your station.\n"
                "Set your app's published URL at the top, then add a class.",
            )

    # ---------------- header ----------------

    def _build_header(self):
        bar = tk.Frame(self.root, bg=ui.PANEL_BG)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(1, weight=1)
        tk.Label(bar, text="App URL", bg=ui.PANEL_BG, fg=ui.DIM,
                 font=("TkDefaultFont", 10)).grid(row=0, column=0, padx=(16, 8), pady=12)
        self.pwa_var = tk.StringVar(value=self.raw.get("pwa_url", ""))
        entry = tk.Entry(bar, textvariable=self.pwa_var, font=("TkFixedFont", 10), **ENTRY_KW)
        entry.grid(row=0, column=1, sticky="ew", ipady=4)
        tk.Button(bar, text="Save URL", command=self.save_pwa_url, bg="#2c2f35", fg=ui.FG,
                  relief="flat", padx=12).grid(row=0, column=2, padx=8)
        tk.Label(bar, text="where the student app is published, e.g. https://you.github.io/mattend-0/",
                 bg=ui.PANEL_BG, fg="#5a5e66", font=("TkDefaultFont", 8)).grid(
            row=1, column=1, sticky="w", pady=(0, 8))

    def save_pwa_url(self):
        url = self.pwa_var.get().strip()
        config_mod.update_raw(self.config_path, pwa_url=url)
        self.raw["pwa_url"] = url
        self.render_qr()

    # ---------------- sidebar ----------------

    def _build_sidebar(self):
        side = tk.Frame(self.root, bg=ui.PANEL_BG, width=250)
        side.grid(row=1, column=0, rowspan=2, sticky="ns")
        side.grid_propagate(False)
        tk.Label(side, text="CLASSES", bg=ui.PANEL_BG, fg=ui.FG,
                 font=("TkDefaultFont", 11, "bold")).pack(anchor="w", padx=16, pady=(16, 8))

        self.listbox = tk.Listbox(side, bg=FIELD_BG, fg=ui.FG, relief="flat", highlightthickness=0,
                                  selectbackground="#33507a", font=("TkDefaultFont", 11),
                                  activestyle="none")
        self.listbox.pack(fill="both", expand=True, padx=12)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self.select_from_list())

        buttons = tk.Frame(side, bg=ui.PANEL_BG)
        buttons.pack(fill="x", padx=12, pady=12)
        for text, command in (("New class", self.new_class), ("Delete", self.delete_class)):
            tk.Button(buttons, text=text, command=command, bg="#2c2f35", fg=ui.FG,
                      relief="flat", pady=6).pack(fill="x", pady=2)
        tk.Button(side, text="▶  Launch Station", command=self.launch, bg="#2a6b3f", fg="#ffffff",
                  relief="flat", pady=10, font=("TkDefaultFont", 11, "bold")).pack(
            fill="x", padx=12, pady=(0, 16))

    # ---------------- editor ----------------

    def _row(self, parent, row, label, hint=""):
        tk.Label(parent, text=label, bg=ui.BG, fg=ui.DIM, font=("TkDefaultFont", 10),
                 anchor="w").grid(row=row, column=0, sticky="w", pady=(8, 0))
        if hint:
            tk.Label(parent, text=hint, bg=ui.BG, fg="#5a5e66", font=("TkDefaultFont", 8),
                     anchor="w").grid(row=row + 1, column=1, sticky="w")

    def _build_editor(self):
        pane = tk.Frame(self.root, bg=ui.BG)
        pane.grid(row=1, column=1, sticky="nsew", padx=24, pady=16)
        pane.columnconfigure(1, weight=1)

        self.vars = {name: tk.StringVar() for name in
                     ("label", "course_cid", "slot", "roster_csv", "delta", "rotate")}

        rows = [
            ("Class name", "label", "shown on the station and in the enrollment QR"),
            ("Course CID", "course_cid", "must match the Class_ID students submit on the form"),
            ("Slot", "slot", "changes C_ID, so two sections on one day stay separate"),
        ]
        row = 0
        for label, key, hint in rows:
            self._row(pane, row, label, hint)
            tk.Entry(pane, textvariable=self.vars[key], font=("TkDefaultFont", 11),
                     **ENTRY_KW).grid(row=row, column=1, sticky="ew", padx=(12, 0), ipady=4)
            row += 2

        self._row(pane, row, "Roster CSV", "the responses export for this class")
        csv_row = tk.Frame(pane, bg=ui.BG)
        csv_row.grid(row=row, column=1, sticky="ew", padx=(12, 0))
        csv_row.columnconfigure(0, weight=1)
        tk.Entry(csv_row, textvariable=self.vars["roster_csv"], font=("TkFixedFont", 10),
                 **ENTRY_KW).grid(row=0, column=0, sticky="ew", ipady=4)
        tk.Button(csv_row, text="Browse", command=self.browse_csv, bg="#2c2f35", fg=ui.FG,
                  relief="flat", padx=10).grid(row=0, column=1, padx=(6, 0))
        row += 2

        timing = tk.Frame(pane, bg=ui.BG)
        timing.grid(row=row, column=1, sticky="w", padx=(12, 0), pady=(8, 0))
        self._row(pane, row, "Timing", "seconds a student has, and how fast the QR rotates")
        for text, key, width in (("scan window", "delta", 5), ("QR rotate", "rotate", 5)):
            tk.Label(timing, text=text, bg=ui.BG, fg="#5a5e66",
                     font=("TkDefaultFont", 9)).pack(side="left", padx=(0, 6))
            tk.Entry(timing, textvariable=self.vars[key], width=width,
                     font=("TkFixedFont", 10), **ENTRY_KW).pack(side="left", padx=(0, 18), ipady=3)
        row += 2

        # ----- Google Form linking -----
        tk.Frame(pane, bg="#2a2c31", height=1).grid(row=row, column=0, columnspan=2,
                                                    sticky="ew", pady=16)
        row += 1
        tk.Label(pane, text="GOOGLE FORM", bg=ui.BG, fg=ui.FG,
                 font=("TkDefaultFont", 10, "bold"), anchor="w").grid(
            row=row, column=0, columnspan=2, sticky="w")
        row += 1
        tk.Label(pane,
                 text="In your form: ⋮ menu → Get pre-filled link → type UUID / NAME / CID into the\n"
                      "matching questions → Get link → Copy link → paste it below.",
                 bg=ui.BG, fg="#5a5e66", font=("TkDefaultFont", 9), justify="left",
                 anchor="w").grid(row=row, column=0, columnspan=2, sticky="w", pady=(2, 8))
        row += 1

        paste = tk.Frame(pane, bg=ui.BG)
        paste.grid(row=row, column=0, columnspan=2, sticky="ew")
        paste.columnconfigure(0, weight=1)
        self.prefill_var = tk.StringVar()
        tk.Entry(paste, textvariable=self.prefill_var, font=("TkFixedFont", 9),
                 **ENTRY_KW).grid(row=0, column=0, sticky="ew", ipady=4)
        tk.Button(paste, text="Read fields", command=self.parse_prefill, bg="#33507a", fg="#ffffff",
                  relief="flat", padx=14).grid(row=0, column=1, padx=(6, 0))
        row += 1

        self.form_status = tk.Label(pane, text="no form linked yet", bg=ui.BG, fg=ui.DIM,
                                    font=("TkFixedFont", 9), anchor="w")
        self.form_status.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 4))
        row += 1

        roles = tk.Frame(pane, bg=ui.BG)
        roles.grid(row=row, column=0, columnspan=2, sticky="w")
        self.role_boxes = {}
        for index, (role, caption) in enumerate(ROLES):
            tk.Label(roles, text=caption, bg=ui.BG, fg=ui.DIM,
                     font=("TkDefaultFont", 9)).grid(row=index, column=0, sticky="w", pady=2)
            var = tk.StringVar()
            box = ttk.Combobox(roles, textvariable=var, state="readonly", width=34)
            box.grid(row=index, column=1, sticky="w", padx=(12, 0), pady=2)
            self.role_vars[role] = var
            self.role_boxes[role] = box
        row += 1

        tk.Button(pane, text="Save class", command=self.save_class, bg="#2c2f35", fg=ui.FG,
                  relief="flat", padx=18, pady=8).grid(row=row, column=0, columnspan=2,
                                                       sticky="w", pady=16)

    # ---------------- enrollment QR ----------------

    def _build_qr(self):
        panel = tk.Frame(self.root, bg=ui.PANEL_BG, width=340)
        panel.grid(row=1, column=2, rowspan=2, sticky="ns")
        panel.grid_propagate(False)
        self.root.columnconfigure(2, weight=0)

        tk.Label(panel, text="ENROLLMENT QR", bg=ui.PANEL_BG, fg=ui.FG,
                 font=("TkDefaultFont", 11, "bold")).pack(anchor="w", padx=16, pady=(16, 2))
        tk.Label(panel, text="students scan this once, to register",
                 bg=ui.PANEL_BG, fg="#5a5e66", font=("TkDefaultFont", 8)).pack(anchor="w", padx=16)

        self.qr_canvas = tk.Canvas(panel, bg="#ffffff", width=280, height=280,
                                   highlightthickness=0)
        self.qr_canvas.pack(padx=16, pady=14)
        self.qr_note = tk.Label(panel, text="", bg=ui.PANEL_BG, fg=ui.DIM, wraplength=300,
                                justify="left", font=("TkFixedFont", 8))
        self.qr_note.pack(anchor="w", padx=16)

        for text, command in (("Show fullscreen", self.show_fullscreen), ("Save PNG", self.save_png)):
            tk.Button(panel, text=text, command=command, bg="#2c2f35", fg=ui.FG,
                      relief="flat", pady=6).pack(fill="x", padx=16, pady=3)

    def enrollment_url(self) -> str | None:
        if self.current is None:
            return None
        try:
            return enroll.build_url(self.current, self.pwa_var.get().strip())
        except ClassroomError:
            return None

    def render_qr(self):
        self.qr_canvas.delete("all")
        url = self.enrollment_url()
        if url is None:
            reason = "pick a class" if self.current is None else self.current.ready[1]
            if self.current is not None and self.current.ready[0] and not self.pwa_var.get().strip():
                reason = "set the App URL at the top"
            self.qr_note.config(text=f"not ready — {reason}")
            self.qr_canvas.create_text(140, 140, text="—", fill="#c9ccd1",
                                       font=("TkDefaultFont", 28))
            return
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        size = len(matrix)
        module = max(1, 280 // size)
        offset = (280 - module * size) // 2
        for r, line in enumerate(matrix):
            c = 0
            while c < size:
                if not line[c]:
                    c += 1
                    continue
                start = c
                while c < size and line[c]:
                    c += 1
                self.qr_canvas.create_rectangle(
                    offset + start * module, offset + r * module,
                    offset + c * module, offset + (r + 1) * module,
                    fill="black", outline="")
        self.qr_note.config(text=f"QR v{qr.version} · {len(url)} chars\n{url}")

    def show_fullscreen(self):
        url = self.enrollment_url()
        if url is None:
            messagebox.showwarning("Not ready", "Finish setting this class up first.")
            return
        window = tk.Toplevel(self.root)
        window.title(f"Enroll - {self.current.label}")
        window.configure(bg=ui.BG)
        window.attributes("-fullscreen", True)
        window.bind("<Escape>", lambda _e: window.destroy())
        window.bind("q", lambda _e: window.destroy())
        panel = ui.QRPanel(window, caption=f"Scan to register · {self.current.label}")
        panel.pack(expand=True, fill="both")
        panel.subheading.config(text=self.current.course_cid)
        panel.footer.config(text="Esc to close")
        panel.show(url)

    def save_png(self):
        url = self.enrollment_url()
        if url is None:
            messagebox.showwarning("Not ready", "Finish setting this class up first.")
            return
        target = filedialog.asksaveasfilename(
            defaultextension=".png", initialfile=f"enroll-{self.current.key}.png",
            filetypes=[("PNG image", "*.png")])
        if not target:
            return
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=3)
        qr.add_data(url)
        qr.make(fit=True)
        qr.make_image(fill_color="black", back_color="white").save(target)
        messagebox.showinfo("Saved", f"Enrollment QR written to\n{target}")

    # ---------------- class CRUD ----------------

    def refresh_list(self, select: str | None = None):
        self.listbox.delete(0, tk.END)
        self.keys = [room.key for room in self.registry]
        for room in self.registry:
            mark = "●" if self.registry.active and room.key == self.registry.active.key else " "
            self.listbox.insert(tk.END, f" {mark} {room.label}")
        target = select or (self.registry.active.key if self.registry.active else None)
        if target in self.keys:
            index = self.keys.index(target)
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(index)
            self.show_class(self.registry.get(target))
        elif not self.keys:
            self.current = None
            self.render_qr()

    def select_from_list(self):
        selection = self.listbox.curselection()
        if selection:
            self.show_class(self.registry.get(self.keys[selection[0]]))

    def show_class(self, room: Classroom | None):
        self.current = room
        if room is None:
            self.render_qr()
            return
        self.vars["label"].set(room.label)
        self.vars["course_cid"].set(room.course_cid)
        self.vars["slot"].set(room.slot)
        self.vars["roster_csv"].set(room.roster_csv)
        self.vars["delta"].set(str(room.delta_t_max_seconds))
        self.vars["rotate"].set(str(room.qr_rotate_seconds))
        self.prefill_var.set("")
        self.parsed_entries = {entry: "" for entry in room.entries.values()}
        self._fill_role_boxes(room.entries)
        if room.form_id:
            self.form_status.config(text=f"form {room.form_id[:26]}…  ({len(room.entries)} fields assigned)",
                                    fg="#4fa96a")
        else:
            self.form_status.config(text="no form linked yet", fg=ui.DIM)
        self.render_qr()

    def _fill_role_boxes(self, assigned: dict[str, str]):
        options = [""] + [f"entry.{entry}  =  {sample}" if sample else f"entry.{entry}"
                          for entry, sample in self.parsed_entries.items()]
        for role, _caption in ROLES:
            self.role_boxes[role]["values"] = options
            entry_id = assigned.get(role)
            match = next((opt for opt in options if opt.startswith(f"entry.{entry_id}  ")
                          or opt == f"entry.{entry_id}"), "")
            self.role_vars[role].set(match)

    def _roles_from_boxes(self) -> dict[str, str]:
        roles = {}
        for role, _caption in ROLES:
            value = self.role_vars[role].get().strip()
            if value.startswith("entry."):
                roles[role] = value.split()[0].removeprefix("entry.")
        return roles

    def new_class(self):
        room = Classroom(key=self.registry.unique_key("new-class"), course_cid="NEW-CLASS",
                         label="New class")
        self.registry.add(room)
        self.registry.save()
        self.refresh_list(select=room.key)

    def delete_class(self):
        if self.current is None:
            return
        if not messagebox.askyesno("Delete class", f"Remove {self.current.label!r}?"):
            return
        self.registry.remove(self.current.key)
        self.registry.save()
        self.current = None
        self.refresh_list()

    def parse_prefill(self):
        try:
            form_id, entries = parse_prefilled_link(self.prefill_var.get())
        except ClassroomError as exc:
            messagebox.showerror("Could not read that link", str(exc))
            return
        self.parsed_entries = entries
        guessed = guess_roles(entries)
        self._fill_role_boxes(guessed)
        self._pending_form_id = form_id
        missing = [caption for role, caption in ROLES[:2] if role not in guessed]
        note = f"found {len(entries)} field(s) in form {form_id[:22]}…"
        if missing:
            note += "  — assign " + ", ".join(missing) + " below"
        self.form_status.config(text=note, fg="#d8a020" if missing else "#4fa96a")

    def browse_csv(self):
        chosen = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("All files", "*")])
        if chosen:
            try:
                chosen = str(Path(chosen).relative_to(self.base))
            except ValueError:
                pass
            self.vars["roster_csv"].set(chosen)

    def save_class(self):
        if self.current is None:
            messagebox.showwarning("No class", "Create a class first.")
            return
        course_cid = self.vars["course_cid"].get().strip().upper()
        if not course_cid:
            messagebox.showerror("Missing", "Course CID is required.")
            return

        room = self.current
        room.label = self.vars["label"].get().strip() or course_cid
        room.course_cid = course_cid
        room.slot = self.vars["slot"].get().strip().upper() or "A"
        room.roster_csv = self.vars["roster_csv"].get().strip() or f"data/{room.key}.csv"
        for key, attribute, floor in (("delta", "delta_t_max_seconds", 3),
                                      ("rotate", "qr_rotate_seconds", 1)):
            try:
                setattr(room, attribute, max(floor, int(self.vars[key].get())))
            except ValueError:
                pass
        if room.key.startswith("new-class"):
            # The placeholder key also names the default roster file, so re-key
            # it as soon as the teacher tells us what the course actually is.
            default_csv = room.roster_csv in ("", f"data/{room.key}.csv")
            new_key = self.registry.rename(room.key, course_cid)
            room.key = new_key
            if default_csv:
                room.roster_csv = f"data/{new_key}.csv"
                self.vars["roster_csv"].set(room.roster_csv)

        if getattr(self, "_pending_form_id", None):
            room.form_id = self._pending_form_id
            self._pending_form_id = None
        roles = self._roles_from_boxes()
        if roles:
            room.entries = roles

        self.registry.save()
        self.show_class(room)
        self.refresh_list(select=room.key)

    def launch(self):
        if self.current is None:
            messagebox.showwarning("No class", "Select a class first.")
            return
        self.save_class()
        self.registry.set_active(self.current.key)
        self.registry.save()
        self.refresh_list(select=self.current.key)
        subprocess.Popen([sys.executable, "-m", "server.app"], cwd=str(self.base.parent))

    def run(self):
        self.root.mainloop()


def main() -> int:
    ClassManager().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
