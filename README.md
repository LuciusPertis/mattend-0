# mattend

Attendance that a phone can't fake from the hostel.

A student has to scan a QR that only exists **in the room**, only for the last
few **seconds**, and then show their phone's reply to a camera at the door.
Miss any of those and it doesn't count.

```
   PROJECTOR                    STUDENT PHONE                  DOOR CAMERA
   ┌─────────┐                  ┌─────────────┐                ┌─────────┐
   │  QR #1  │ ──── scan ─────► │  fuse with  │ ──── show ───► │  QR #2  │
   │ room +  │                  │  their ID + │                │ verify  │
   │  time   │                  │  the time   │                │ + log   │
   └─────────┘                  └─────────────┘                └─────────┘
   pc_out.py                    docs/ (the PWA)                pc_in.py
```

---

## Three kinds of QR

| QR | when | what it carries |
|---|---|---|
| **Enrollment** | once per course | which class, and which Google Form to register on |
| **Source** | every few seconds in class | the room and the current time, encrypted |
| **Response** | on the student's phone | the source blob + their device id + when they scanned |

## The pieces

| piece | runs on | what it is |
|---|---|---|
| **Class Manager** | your machine, once per class | `python3 -m server.admin` — add classes, link your Google Form, print the enrollment QR |
| **The station** | your lab machine, every class | `python3 -m server.app` — one window: QR on the left, verifications on the right |
| **The phone app** | student's phone | a web page at `https://<you>.github.io/mattend-0/` — scans QR #1, produces QR #2 |

That's it for the normal case — **one command**. The station shows the QR *and*
watches the webcam.

Running the two halves on separate machines instead? Use `python3 -m server.pc_out`
on the projector and `python3 -m server.pc_in` on the scanner, and give **both the
same `config.json`** — same secret, same session, or nothing verifies.

---

## Try it in 5 minutes, no hardware

Before you touch a projector or a webcam, check the whole pipeline works:

```bash
git clone https://github.com/LuciusPertis/mattend-0
cd mattend-0
pip install "qrcode[pil]" opencv-python

python3 -m server.simulate
```

You should see all six outcomes, computed end to end in software:

```
① pass            OK            | IEC 2026 025 | SHAYAN M | dT=3s | (ΔT 3s)
② user not found  UNF           | USER NOT FOUND | uuid 99999999… not registered
③ MUoP            MUOP          | IEC 2026 007 | KARTHIK V | 2 devices on iec2026007@…
④ timeout         TO            | IEC 2026 025 | SHAYAN M | ΔT 45s > 15s
⑤ wrong session   WRONG_SESSION | INVALID CODE | C_ID mismatch
⑥ garbage         UNREADABLE    | INVALID CODE | authentication tag mismatch
```

If that works, the logic is sound and everything below is just wiring.

---

## One-time setup

### 1. Install

```bash
pip install "qrcode[pil]" opencv-python
sudo apt install python3-tk        # for the projector window
```

### 2. Make your keys

```bash
python3 -m server.config --new-secrets
```

Copy `server/config.example.json` to `server/config.json` and paste both keys in.

> **`pc_secret_hex`** stays on your lab PCs and nowhere else. It's the key that
> makes QR #1 unforgeable — a phone that had it could mint its own room codes.
>
> **`app_secret_hex`** also goes into `docs/protocol.js`, at the top:
> ```js
> const APP_SECRET_HEX = "paste the same app_secret_hex here";
> ```
> These two **must match** or PC (in) can't read a single phone.

### 3. Publish the phone app

Push, then in **Settings → Pages** set source to **`main` branch, `/docs` folder**.
Your app lands at `https://<you>.github.io/mattend-0/`.

Check the client agrees with the server — open `.../selftest.html` on a phone:

```bash
python3 -m server.make_vectors     # regenerate whenever you change the secret
```

Every line must say PASS. If any fails, the two halves disagree and no phone
will verify.

### 4. Add your classes

```bash
python3 -m server.admin
```

Set **App URL** to where you published the app, then **New class**:

- **Class name** and **Course CID** — the CID must match the `Class_ID` students
  submit on your form.
- **Google Form** — this is the only fiddly bit, and the Class Manager does the
  fiddly part for you. In your form: **⋮ → Get pre-filled link**, type the words
  `UUID`, `NAME` and `CID` into the matching questions, **Get link → Copy link**,
  then paste it in and press **Read fields**. It reads out the `entry.NNN` ids
  Google never shows you and assigns them by the words you typed.

Press **Save class** and the **enrollment QR** appears on the right. **Save PNG**
to print it, or **Show fullscreen** to put it on the projector.

Your classes live in `server/classes.json`; you never have to edit it by hand.

### 5. Students join

They scan the enrollment QR — with their phone camera, or with the app's own
scanner if they already have it. Either way the app opens, shows which class
they're joining, asks for their name, and sends them to *your* form with their
device id prefilled.

One device, many classes: the UUID is created once and reused, so a student
scans a different enrollment QR per course and the app remembers them all.

Your form needs a column for each of:

| meaning | headers that match | example |
|---|---|---|
| when | `Timestamp` | `8/20/2026 11:00:09` |
| who | anything with **email** | `pmr2025001@iiita.ac.in` |
| device | `UUID`, `msL-key`, anything with **key** or **device** | `f76b4844-25c3-…` |
| name | anything with **name** | `Shubhadeep Sarkar` |
| class | `CID`, `Class_ID`, anything with **class** / **course** / **section** | `PSP-LAB-SEC-D` |

Header matching is fuzzy and order-independent, so renaming a form question
won't break the loader. Two things it handles for you:

- **A junk device column.** A student who types anything that isn't a real UUID
  is dropped and counted, not loaded. This matters: keeping it would inflate
  that student's device count and wrongly flag them as MUoP.
- **A stale header row** above the form's own header. It uses the row directly
  above the first row containing an email, so a leftover label row that lists
  the columns in a *different order* can't silently swap names and UUIDs.

The roll number on the verdict cards comes from the email local part —
`pmr2025001@…` renders as `PMR 2025 001`.

---

## Every class

### 1. Pick the class

Open the Class Manager, select the class, press **▶ Launch Station**. That's it —
**the date is always today**, so nothing needs editing between classes. It's
still what stops yesterday's QR working today.

```bash
python3 -m server.config      # prints the session it resolved, sanity-check it
```

### 2. Refresh the roster

Google Form → Responses → download CSV → save as `server/data/responses.csv`.

### 3. Launch

```bash
python3 -m server.admin       # pick a class, press Launch Station
python3 -m server.app         # or go straight to the active class
```

The window opens maximised but stays a normal window, so you can minimise it.
Point the projector at the left half and the webcam at the queue.

### Scanning twice

Three different things can look like "that phone again", and they behave
differently:

| situation | what happens |
|---|---|
| the phone is **still in front of the camera** | nothing, silently — no card spam |
| they **already passed** and scan again later | a green *ALREADY MARKED* card appears above the queue and vanishes after a few seconds; their pass is untouched |
| they **previously failed** (timeout, not registered) and retry | a fresh verdict, exactly as if it were their first scan — so *TIMEOUT — TRY AGAIN* actually works |

A pass is never downgraded by a later scan, and nobody is ever counted twice.

| key | does |
|---|---|
| `r` | **reload the roster** — a student who registers mid-class shows up without a restart |
| `e` | export the CSV right now |
| `c` | clear the verification cards off screen |
| `F11` | true fullscreen · `Esc` leaves it |
| `q` | quit (exports on the way out) |

The panel on the left shows a small camera preview so you can aim it.

### 4. Collect

On exit, `pc_in` writes:

- `server/data/attendance.sqlite3` — the full log
- `server/data/attendance_export.csv` — open in a spreadsheet

---

## What the student sees

1. Open the link. Enter name **once** — it registers the device and opens the form.
2. **Start Camera** → point at the projector.
3. Their phone shows QR #2 with a countdown. Walk to the door camera before it runs out.
4. If it expires, **Reset Pipeline** and scan the projector again.

---

## What the cards mean

The right half of the window is a queue. The **newest four** verdicts show large;
older ones shrink to a single line and eventually scroll off the bottom.

| card | colour | meaning | what to do |
|---|---|---|---|
| roll + name | 🟢 green | present | nothing, they're marked |
| `USER NOT FOUND` | ⚪ grey | device isn't on this course's roster | they never registered, or registered under a different CID |
| roll + `MUoP REPORTED` | 🔴 red | that email has **two devices registered** | likely proxy — logged and named, follow up |
| roll + `TIMEOUT` | 🟡 yellow | too long between scanning the projector and reaching the camera | send them back to rescan |
| `INVALID CODE` | ⚫ slate/black | wrong room or day, or not one of our QRs at all | check `pc_out` is running today's session |

Red and yellow are still **logged with the student's name** — flagged, not
thrown away. And a pass is never undone by a later timeout, so someone scanning
again at the end of class keeps their mark.

---

## When something goes wrong

| symptom | cause | fix |
|---|---|---|
| **Everyone** gets `INVALID CODE` | `app_secret_hex` in `config.json` ≠ `APP_SECRET_HEX` in `docs/protocol.js` | make them match, `make_vectors`, hard-refresh the phone |
| Everyone gets `WRONG SESSION` | two-machine setup with different `session` blocks | same `config.json` on both machines |
| Everyone gets `TIMEOUT` | the **station's clock** is off, or `delta_t_max_seconds` is too tight | `timedatectl set-ntp true`; try raising the window |
| One student gets `USER NOT FOUND` | not registered, or wrong CID on the form | check the CSV, then press `r` |
| Phone shows "Could not fuse" | it scanned some other QR, not the projector | rescan |
| Phone app won't open the camera | needs HTTPS | use the `github.io` URL, not a local file |
| `cannot open camera 0` | wrong index, or another app holds the webcam | `python3 -m server.app --list-cameras`, set `camera_index` |
| Projector QR won't scan | too small, or a screensaver dimmed it | `F11` fullscreen, disable sleep |
| Window opens but no camera preview | webcam busy or absent | close other apps using it, then `--list-cameras` |

Only the **phone's** clock and the **projector PC's** clock matter for timing.
The door camera's clock is irrelevant — it never looks at its own time.

---

## Tuning

In `server/config.json`:

| setting | in `config.example.json` | what it does |
|---|---|---|
| `delta_t_max_seconds` | `90` | how long a student has between projector and door. Lower is stricter |
| `qr_rotate_seconds` | `5` | how often QR #1 changes |
| `camera_index` | `0` | which webcam — see `python3 -m server.app --list-cameras` |

If you lower `delta_t_max_seconds`, also lower `CAPTURE_WINDOW_SECONDS` in
`docs/index.html` so the phone's countdown tells the truth.

---

## What it stops, and what it doesn't

**Stops:** yesterday's QR, another room's QR, a screenshot from earlier, a phone
minting its own room code, and one person registering a second device for a friend.

**Doesn't stop:** a student who *is* in the room forwarding QR #1 to an absent
friend over chat inside the ΔT window. Lowering `delta_t_max_seconds` narrows
that gap; closing it completely would need something a phone can't forward.

Also: `app_secret` ships inside the web page, so anyone who reads the page source
can build a QR #2. They still can't produce a valid QR #1 without being in the
room, so the guarantees above hold.

---

## Layout

```
docs/         the phone app — this folder is what GitHub Pages publishes
server/       both PC halves and the verification logic
qr*.py        early latency benchmarks, not part of the system
```

Design notes, the crypto, and the verdict precedence rules are in
[server/README.md](server/README.md).

```bash
python -m unittest server.test_server     # 36 tests
```
