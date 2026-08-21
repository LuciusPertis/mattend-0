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
| `config.py` | secrets, session, and `C_ID` derivation from lab/class/day |
| `protocol.py` | the two hops end to end |
| `roster.py` | Google Form CSV → per-CID roster, UNF and MUoP indexes |
| `verify.py` | the verdict pipeline and its precedence |
| `store.py` | local dump `UUID │ CID │ Gen_T │ Cap_T`, one row per student |
| `app.py` | **the operator GUI** — QR left, verdict queue right, one Tk window |
| `ui.py` | Tk widgets: `QRPanel`, `VerdictQueue` |
| `camera.py` | capture + decode on a background thread |
| `pc_out.py` | projector-only view, for a two-machine setup |
| `pc_in.py` | headless scanner, for a two-machine setup or debugging |
| `simulate.py` | all six verdicts with no camera and no phone |
| `make_vectors.py` | interop vectors for `docs/selftest.html` |
| `test_server.py` | 36 unit tests |

## Setup

```bash
pip install qrcode opencv-python          # tkinter comes from python3-tk

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

Export the Google Form responses to CSV and save as `data/responses.csv`
(columns `Timestamp, Email, Name, UUID, CID` — header matching is fuzzy).

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
