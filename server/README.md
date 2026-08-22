# mattend · server side

The two PC halves of the relay, plus the verification pipeline between them.

```
PC (out)  ──── Z*_{CID,GT} = P_c( C_ID ‖ Gen_T ) ────►  phone
                                                          │
                                                          │ Z*_s = P_s( Z*_{CID,GT} ‖ UUID ‖ Cap_T )
                                                          ▼
                                                       PC (in)
```

A phone cannot fake attendance because the inner blob only exists where the
projector is, only for the last few seconds, and only the two lab PCs hold the
key that made it.

## Layout

| file | role |
|---|---|
| `crypto.py` | `P_c` / `P_s` — SIV-mode authenticated encryption, 6-byte overhead |
| `codec.py` | bit-packed fields, Base32 for QR alphanumeric mode |
| `config.py` | this teacher's secrets and machine settings; merges in the active class |
| `classroom.py` | the class registry, `C_ID` derivation, and the prefilled-link parser |
| `enroll.py` | the enrollment URL: build, parse, and the student's prefill link |
| `admin.py` | **Class Manager** — add classes, link a form, print the enrollment QR, launch |
| `protocol.py` | the two hops end to end |
| `roster.py` | Google Form CSV → per-CID roster, UNF and MUoP indexes |
| `ui.py` | `QRPanel`, `MetricsPanel`, `VerdictQueue` |
| `verify.py` | the verdict pipeline and its precedence |
| `ledger.py` | rescan rules — silent / repeat / retry — and the session tallies |
| `store.py` | local dump `UUID │ CID │ Gen_T │ Cap_T`, one row per student |
| `app.py` | **the operator GUI** — QR left, verdict queue right, one Tk window |
| `ui.py` | Tk widgets: `QRPanel`, `VerdictQueue` |
| `camera.py` | capture + decode on a background thread |
| `pc_out.py` | projector-only view, for a two-machine setup |
| `pc_in.py` | headless scanner, for a two-machine setup or debugging |
| `simulate.py` | all six verdicts with no camera and no phone |
| `make_vectors.py` | interop vectors for `docs/selftest.html` |
| `test_server.py` | 36 unit tests |

## Configuration split

Nothing about a class is compiled in. Two files, with different lifetimes:

| file | scope | holds |
|---|---|---|
| `config.json` | **this teacher, this machine** | `pc_secret`, `app_secret`, `pwa_url`, camera |
| `classes.json` | **this teacher's classes** | one entry per class, and which is active |

Both are gitignored; `.example.json` versions are committed. `pc_secret` is
minted per teacher on first run, so a teacher's source QRs verify only on their
own station — another teacher's projector cannot mark their class.

A pre-registry `config.json` with a `session` block is folded into the registry
automatically on first load, so existing setups keep working untouched.

`date` defaults to the string `"today"` and resolves per run. That removes the
one daily chore while keeping the property that matters: yesterday's QR has a
different `C_ID` and will not verify.

## The enrollment QR

A third QR type, static and printable, carrying a plain URL:

```
https://<host>/mattend-0/?e=1&cid=PSP-LAB-SEC-D&l=PSP+Lab&f=<formId>&u=<entry>&n=<entry>&c=<entry>
```

Plain rather than an encoded blob, because that buys three things:

- an ordinary phone camera opens it, onboarding a student who has nothing installed;
- the app's own scanner recognises it too, so an installed student never leaves the app;
- a student can read it and see which form they are about to submit to.

That last point is the honest mitigation for it being **unsigned**. Signing would
need a key on the phone, and `app_secret` is already public JavaScript, so a
signature would prove nothing. A forged enrollment QR can misdirect a
*registration*; it cannot forge attendance, which still needs `pc_secret`.

Sized at QR **v8–v10** — fine from a printed A5 or a projector.

### Google Form field ids

`entry.954365518` is not shown anywhere in the Form editor, which makes this the
worst part of setup. `parse_prefilled_link` reads the ids out of the URL that
Forms' own **Get pre-filled link** produces, and `guess_roles` assigns them by
whatever sample text the teacher typed (`UUID`, `NAME`, `CID`). The teacher never
sees an entry id.

## Setup

```bash
pip install qrcode opencv-python          # tkinter comes from python3-tk

python3 -m server.admin       # creates config.json with your own secrets on first run
```

The Class Manager bootstraps everything. To do it by hand instead:

```bash
cd mattend-0/server
cp config.example.json config.json
python3 -m server.config --new-secrets     # paste both into config.json
```

Then bake the **same** `app_secret_hex` into `docs/protocol.js`
(`APP_SECRET_HEX`). `pc_secret_hex` never leaves the two lab PCs — that is the
key the phone must not have.

Set the session for the day:

```json
"session": { "course_cid": "IEC-2026-LAB", "date": "2026-08-21", "slot": "A" }
```

`course_cid` must match the CID string students type into the registration form.
`C_ID` is `blake2s(course_cid | date | slot)[:4]` — change any of the three and
yesterday's QR stops verifying.

Export the Google Form responses to CSV and save as `data/responses.csv`.

### What the loader tolerates

The real export is messier than it looks, so `roster.py` is defensive about:

- **Renamed columns.** Matching is alias-based: `UUID`/`msL-key`/`*key*`/`*device*`,
  `CID`/`Class_ID`/`*class*`/`*course*`/`*section*`, and so on.
- **More than one header row.** The header is taken as the row directly above
  the first row containing an `@`. A hand-written label row above the form's own
  header can list the columns in a *different order*; trusting it loads names as
  UUIDs and every student silently fails with UNF.
- **Junk in the device column.** Rows whose UUID doesn't parse are rejected and
  counted (`roster.rejected`), never loaded. Keeping them would inflate an
  email's device count and raise a false MUoP.
- **Locale-dependent dates.** A slashed stamp can be `M/D/Y` or `D/M/Y` and the
  file doesn't say which, so the order is *inferred from the data*: any
  component above 12 can't be a month and settles it for the whole file. With no
  such row it reports `M/D/Y (assumed)`. Shown live on the `ROSTER` metrics line.
  An unparseable stamp yields `None`, never an error — timestamps only order
  re-registrations, so nothing breaks if they're missing.

## Running a class

```bash
python3 -m server.app        # one machine: QR + scanner in one window
```

Keys: `q` quit · `e` export · `r` reload roster · `c` clear cards · `F11` fullscreen.
It opens maximised via `-zoomed` rather than `-fullscreen`, so it still minimises.

Two machines instead:

```bash
python3 -m server.pc_out     # projector
python3 -m server.pc_in      # scanner, headless
```

Either way you get `data/attendance.sqlite3` and `data/attendance_export.csv`.

### Rescans

`ledger.py` splits "seen this UUID before" into three cases, because collapsing
them into one silent `return` gets two of them wrong:

| | when | result |
|---|---|---|
| `SILENT` | within `cooldown_seconds` (3s) | the same phone is still in frame — ignore it |
| `REPEAT` | already passed | keep the pass, show a vanishing banner, bump `scan_count` |
| `SHOW` | previously failed | a full fresh verdict |

That last row is the one that matters: a `TO` card tells the student to try
again, so the retry has to be allowed. Tallies are derived from the ledger's
current verdict per device rather than incremented, so no path can double-count.

### Threading

Tk is single-threaded and its mainloop must not block, but
`detectAndDecodeMulti` costs tens of milliseconds a frame. So `camera.py` runs
capture and decode on its own thread and posts payload strings to a
`queue.Queue`; `app.drain()` empties it on a 60 ms `after` timer. Nothing
outside the main thread ever touches a Tk widget.

### Why there is no OpenCV window

`cv2.imshow` is not used anywhere. On an OpenCV built against Qt5, a window
title containing a non-ASCII character breaks the internal window lookup and
`imshow` spawns a **new window every frame** — measured at 58 windows in 3
seconds with `"mattend · PC (in)"` as the title, against 1 for a plain ASCII
name. The GUI is Tk throughout, so the whole class of bug is gone.

## Verdicts

| | condition | card | colour |
|---|---|---|---|
| ① `OK` | everything checks out | roll + name | green |
| ② `UNF` | UUID not on this course's roster | `USER NOT FOUND` | grey |
| ③ `MUOP` | registered email has >1 device | roll + name + `MUoP REPORTED` | red |
| ④ `TO` | ΔT over `delta_t_max_seconds` | roll + name + `TIMEOUT — TRY AGAIN` | yellow |
| ⑤ `WRONG_SESSION` | `C_ID` is another room or day | `INVALID CODE` | slate |
| ⑥ `UNREADABLE` | not a mattend QR, or forged | `INVALID CODE` | near-black |

⑤ and ⑥ are not in the original diagram; they are the two ways a payload can
fail before there is any identity to report. Precedence is
`UNREADABLE → WRONG_SESSION → UNF → MUOP → TO → OK`: identity problems outrank
the timing problem, because `TO` is a "try again" while `MUoP` is a report.

③ and ④ are still recorded to the dump with the student named — they are
flagged, not dropped. A pass already written is never downgraded by a later
timeout, so a student who scans again at the end of class keeps their mark.

## Checking it works

```bash
python3 -m unittest server.test_server -v  # 36 tests
python3 -m server.simulate                 # all six verdicts, no hardware
python3 -m server.simulate --png            # writes data/sim_*.png to point a camera at
python3 -m server.config                   # print the resolved session and C_ID
```

For the client half, run `python3 -m server.make_vectors`, serve `docs/`
over HTTP and open `selftest.html` on the phone. Every vector must pass or
`pc_in` will not be able to read that device's QR.

## Payload sizes

| | plaintext | packed | Base32 | QR |
|---|---|---|---|---|
| source `Z*_{CID,GT}` | 9 B | 15 B | 24 chars | **v1** (21×21) |
| response `Z*_s` | 36 B | 42 B | 68 chars | **v3** (29×29) |

The source QR staying at version 1 is what makes the projector hop readable from
the back of the room. `test_qr_size_budget` fails the build if a field change
pushes either past its ceiling.

## What this does and does not stop

Stops: replaying a QR from another room or another day (`C_ID` check), holding a
screenshot from earlier (ΔT), a phone minting its own source QR (it lacks
`pc_secret`), and a student registering a second device for a friend (MUoP).

Does not stop: a student who is physically present relaying the source QR to an
absent friend over chat within the ΔT window. Shrinking `delta_t_max_seconds`
narrows that window; closing it entirely needs a channel the phone cannot
forward, which is out of scope here.

`app_secret` is in client-side JavaScript, so treat it as obfuscation rather
than a secret: anyone who reads the page source can craft a `Z*_s`. They still
need a live inner blob from the room, so the guarantees above hold — this was a
deliberate trade for keeping the Google Form at four fields.
