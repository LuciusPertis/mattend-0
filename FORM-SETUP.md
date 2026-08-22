# Google Form setup

mattend reads your form's **responses CSV**. It matches columns by keyword, not
by position, so you can name questions how you like — within limits. This page
is the limits.

**Use the names in the first table and nothing here can go wrong.**

---

## The six columns

| # | question | who fills it | example |
|---|---|---|---|
| 1 | *(automatic)* `Timestamp` | Google | `8/20/2026 11:00:09` |
| 2 | *(automatic)* `Email Address` | Google | `pmr2025001@iiita.ac.in` |
| 3 | `msL-key` | the app | `f76b4844-25c3-4aa4-a7bb-188f661cba7d` |
| 4 | `msL-pub` | the app | `A0irv0A5vz6Q_3VnbG14QVt7liXh1mUUJGf8aJacxlTz` |
| 5 | `Full Name` | the student | `Shubhadeep Sarkar` |
| 6 | `Class_ID` | the app | `PSP-LAB-SEC-D` |

Columns 3–6 are **short answer** questions. Only column 5 is really typed by the
student; 3, 4 and 6 arrive pre-filled from the enrollment QR, so students never
see or edit them in practice.

### Form settings

- **Collect email addresses → On.** This is column 2, and it's what MUoP
  detection groups by. Without it, one person registering five devices is
  invisible.
- **Limit to 1 response → Off.** Students legitimately re-register — new phone,
  cleared browser, a second course.

### Why `msL-pub`

`msL-key` says *which device*. `msL-pub` is that device's public signing key and
proves the reply really came from it. Both are the same for a student across all
their courses; only `Class_ID` differs between rows.

If you skip `msL-pub`, everything still works — the station just can't verify
signatures, so anyone who reads the app's page source could submit as anyone.
Leave it out only if you're rolling this out gradually.

---

## Linking the form to a class

The Class Manager needs Google's internal `entry.NNN` field ids, which the form
editor never shows you. It reads them out of a prefilled link:

1. In your form: **⋮ → Get pre-filled link**
2. Type these **exact words** as the answers:

   | question | type this |
   |---|---|
   | `msL-key` | `UUID` |
   | `msL-pub` | `PUB` |
   | `Full Name` | `NAME` |
   | `Class_ID` | `CID` |

3. **Get link → Copy link**
4. Paste into the Class Manager and press **Read fields**.

The words are how each field gets assigned to the right role. Check the four
dropdowns afterwards — you can always fix an assignment by hand.

---

## What the column matcher accepts

Matching is case-insensitive and looks for the keyword **anywhere** in the header.

| role | matches a header containing | but **not** if it also contains |
|---|---|---|
| timestamp | `timestamp`, `time stamp`, `submitted` | — |
| email | `email`, `e-mail`, `mail` | — |
| device key | `pub`, `public` | — |
| device id | `uuid`, `uvid`, `msl-key`, `-key`, `device` | `pub`, `public` |
| name | `name` | `cid`, `class`, `course`, `section`, `key`, `pub` |
| class id | `cid`, `class`, `course`, `section` | — |

So `Device Key` / `Public Key` / `Student Name` / `Course Code` all work fine.

### Names that will bite you

The exclusions exist because these are genuinely ambiguous:

| header | problem |
|---|---|
| `Course Name` | contains both `course` and `name` — excluded from **name**, so it's read as the class id |
| `Section Name` | same |
| `Public Key` | contains `key` — excluded from **device id**, so it's read as the device key |

If a header is excluded from every role that needs it, loading **fails with a
clear error** naming the missing role. It will not guess and silently load your
students' names into the UUID column.

### Two header rows

If you keep a hand-written label row above the form's own header, mattend uses
the row directly above the first row containing an `@`. A stale label row listing
columns in a *different order* is therefore ignored rather than trusted.

---

## What a good CSV looks like

```csv
Timestamp,Email Address,msL-key,msL-pub,Full Name,Class_ID
8/20/2026 11:00:09,pmr2025001@iiita.ac.in,f76b4844-25c3-4aa4-a7bb-188f661cba7d,A0irv0A5vz6Q_3VnbG14QVt7liXh1mUUJGf8aJacxlTz,Shubhadeep Sarkar,PSP-LAB-SEC-D
8/20/2026 11:39:55,pmm2025002@iiita.ac.in,eeaa3f6b-3170-49c5-a8fd-6aa7544248d0,AhQ2n4pOxs1kZ0rTgWc7YvB3LmQeR8dFuNaXyKpJz1Hw,Test Anjul,PSP-LAB-SEC-D
```

Save it as the **Roster CSV** path shown in the Class Manager, then press **r**
in the station to reload without restarting.

---

## Troubleshooting

| symptom | cause |
|---|---|
| `roster CSV has no column for ['uuid']` | no question matching `msl-key` / `device` / `-key` |
| `roster CSV has no column for ['name']` | your name question is called something like `Course Name` |
| Everyone shows **USER NOT FOUND** | the CSV's `Class_ID` doesn't match the class's Course CID |
| One student shows **MUoP** unexpectedly | they registered twice — new phone or cleared browser |
| `1 rejected` on the station's ROSTER line | a row's `msL-key` isn't a valid UUID; that row is skipped |
| **DEVICE KEY MISMATCH** for everyone | `msL-pub` column present but holding the wrong thing |

Dates: `8/20/2026` and `2026-08-20` both parse. Whether `9/8/2026` means
September 8 or August 9 is inferred from the rest of the file — the station's
`ROSTER` line shows which reading it chose.
