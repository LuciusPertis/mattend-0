"""python3 -m unittest server.test_server -v"""

from __future__ import annotations

import csv
import json
import tempfile
import time
import unittest
from pathlib import Path

from datetime import datetime

from . import camera as camera_mod, classroom as classroom_mod, codec, crypto
from . import keys as keys_mod
from . import enroll as enroll_mod, ledger as ledger_mod
from . import roster as roster_mod, verify as verify_mod
from .config import Config, Session, derive_cid
from .protocol import make_response_qr, make_source_qr, open_response_qr
from .roster import RosterError, Student, load as load_roster
from .store import Store
from .verify import Verifier

PC_SECRET = bytes(range(32))
APP_SECRET = bytes(range(32, 64))

ALICE = "11111111-1111-4111-8111-111111111111"
BOB_A = "22222222-2222-4222-8222-222222222222"
BOB_B = "33333333-3333-4333-8333-333333333333"
GHOST = "99999999-9999-4999-8999-999999999999"

ROWS = [
    ("2026-08-20 09:01", "iec2026025@iiita.ac.in", "Alice K", ALICE, "IEC-2026-LAB"),
    ("2026-08-20 09:02", "iec2026039@iiita.ac.in", "Bob N", BOB_A, "IEC-2026-LAB"),
    ("2026-08-20 09:03", "iec2026039@iiita.ac.in", "Bob N", BOB_B, "IEC-2026-LAB"),
    ("2026-08-20 09:04", "iec2026061@iiita.ac.in", "Cara P", GHOST, "IEC-2026-THEORY"),
]


def write_roster(directory: Path) -> Path:
    path = directory / "responses.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Timestamp", "Email Address (IIITA)", "Name", "UUID", "CID"])
        writer.writerows(ROWS)
    return path


class CryptoTests(unittest.TestCase):
    def test_roundtrip(self):
        for size in (0, 1, 9, 36, 200):
            payload = bytes(range(size % 256)) * 1 or b""
            payload = payload[:size].ljust(size, b"\x00")
            blob = crypto.pack(PC_SECRET, crypto.DOMAIN_PC, payload)
            self.assertEqual(len(blob), crypto.TAG_LEN + size)
            self.assertEqual(crypto.unpack(PC_SECRET, crypto.DOMAIN_PC, blob), payload)

    def test_deterministic(self):
        a = crypto.pack(PC_SECRET, crypto.DOMAIN_PC, b"same")
        b = crypto.pack(PC_SECRET, crypto.DOMAIN_PC, b"same")
        self.assertEqual(a, b)

    def test_ciphertext_is_not_plaintext(self):
        payload = b"\x00" * 16
        blob = crypto.pack(PC_SECRET, crypto.DOMAIN_PC, payload)
        self.assertNotEqual(blob[crypto.TAG_LEN:], payload)

    def test_wrong_key_rejected(self):
        blob = crypto.pack(PC_SECRET, crypto.DOMAIN_PC, b"secret payload")
        with self.assertRaises(crypto.BadPacket):
            crypto.unpack(APP_SECRET, crypto.DOMAIN_PC, blob)

    def test_domain_separation(self):
        blob = crypto.pack(PC_SECRET, crypto.DOMAIN_PC, b"secret payload")
        with self.assertRaises(crypto.BadPacket):
            crypto.unpack(PC_SECRET, crypto.DOMAIN_MOBILE, blob)

    def test_every_single_bit_flip_is_caught(self):
        blob = bytearray(crypto.pack(PC_SECRET, crypto.DOMAIN_PC, codec.encode_inner(7, 1700000000)))
        for index in range(len(blob)):
            for bit in range(8):
                mutated = bytearray(blob)
                mutated[index] ^= 1 << bit
                with self.assertRaises(crypto.BadPacket):
                    crypto.unpack(PC_SECRET, crypto.DOMAIN_PC, bytes(mutated))

    def test_truncated_rejected(self):
        with self.assertRaises(crypto.BadPacket):
            crypto.unpack(PC_SECRET, crypto.DOMAIN_PC, b"\x00" * 3)


class CodecTests(unittest.TestCase):
    def test_base32_roundtrip(self):
        for size in (1, 15, 42):
            raw = bytes((i * 37) % 256 for i in range(size))
            text = codec.b32encode(raw)
            self.assertNotIn("=", text)
            self.assertTrue(text.isalnum() and text.upper() == text)
            self.assertEqual(codec.b32decode(text), raw)

    def test_inner_roundtrip(self):
        self.assertEqual(codec.decode_inner(codec.encode_inner(0xDEADBEEF, 1700000000)), (0xDEADBEEF, 1700000000))

    def test_outer_roundtrip(self):
        blob = crypto.pack(PC_SECRET, crypto.DOMAIN_PC, codec.encode_inner(1, 2))
        signature = bytes(range(64))
        plaintext = codec.encode_outer(blob, ALICE, 1700000005, 1700000009, signature)
        inner, uuid, cap_t, sub_t, sig, signed = codec.decode_outer(plaintext)
        self.assertEqual((inner, uuid, cap_t, sub_t, sig), (blob, ALICE, 1700000005, 1700000009, signature))
        self.assertEqual(signed, codec.encode_signed(blob, ALICE, 1700000005, 1700000009))

    def test_old_protocol_version_is_rejected_clearly(self):
        blob = crypto.pack(PC_SECRET, crypto.DOMAIN_PC, codec.encode_inner(1, 2))
        stale = bytes([1]) + codec.encode_outer(blob, ALICE, 5, 6)[1:]
        with self.assertRaises(codec.BadPayload) as caught:
            codec.decode_outer(stale)
        self.assertIn("out of date", str(caught.exception))

    def test_qr_size_budget(self):
        """The inner QR must stay at version 1 or the projector hop gets hard to read."""
        import qrcode

        _pub, private = keys_mod.generate_device_key()
        source, _ = make_source_qr(PC_SECRET, 0xFFFFFFFF, gen_t=0xFFFFFFFF)
        response = make_response_qr(APP_SECRET, source, ALICE, 0xFFFFFFFF, 0xFFFFFFFF, private)
        # The response grew to v6 when 64-byte device signatures arrived; a phone
        # screen still gives a 640x480 webcam ~5 px per module at that size.
        for text, ceiling in ((source, 1), (response, 6)):
            qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=2)
            qr.add_data(text)
            qr.make(fit=True)
            self.assertLessEqual(qr.version, ceiling, f"{len(text)} chars -> v{qr.version}")

    def test_bad_base32_rejected(self):
        with self.assertRaises(codec.BadPayload):
            codec.b32decode("1890!!!")

    def test_wrong_length_rejected(self):
        with self.assertRaises(codec.BadPayload):
            codec.decode_inner(b"\x01\x02")
        with self.assertRaises(codec.BadPayload):
            codec.decode_outer(b"\x01" * 10)


class SessionTests(unittest.TestCase):
    def test_cid_is_stable_and_case_insensitive(self):
        self.assertEqual(derive_cid("IEC-2026-LAB", "2026-08-21", "A"), derive_cid("iec-2026-lab", "2026-08-21", "a"))

    def test_cid_changes_with_day_and_slot(self):
        base = derive_cid("IEC-2026-LAB", "2026-08-21", "A")
        self.assertNotEqual(base, derive_cid("IEC-2026-LAB", "2026-08-22", "A"))
        self.assertNotEqual(base, derive_cid("IEC-2026-LAB", "2026-08-21", "B"))
        self.assertNotEqual(base, derive_cid("IEC-2026-THEORY", "2026-08-21", "A"))


class ProtocolTests(unittest.TestCase):
    def test_two_hop_roundtrip(self):
        c_id = derive_cid("IEC-2026-LAB", "2026-08-21", "A")
        source, gen_t = make_source_qr(PC_SECRET, c_id)
        response = make_response_qr(APP_SECRET, source, ALICE, gen_t + 4)
        relay = open_response_qr(PC_SECRET, APP_SECRET, response)
        self.assertEqual((relay.device_uuid, relay.c_id, relay.gen_t, relay.delta_t), (ALICE, c_id, gen_t, 4))

    def test_phone_cannot_read_the_inner_blob(self):
        """The phone relays Z*_{CID,GT} without ever learning C_ID or Gen_T."""
        source, gen_t = make_source_qr(PC_SECRET, 0x1234, gen_t=1700000000)
        with self.assertRaises(crypto.BadPacket):
            crypto.unpack(APP_SECRET, crypto.DOMAIN_PC, codec.b32decode(source))


class RosterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = write_roster(Path(self.tmp.name))
        self.roster = load_roster(self.path, "IEC-2026-LAB")

    def tearDown(self):
        self.tmp.cleanup()

    def test_filters_by_cid(self):
        self.assertEqual(len(self.roster), 3)
        self.assertIsNone(self.roster.lookup(GHOST))

    def test_lookup_and_roll_formatting(self):
        self.assertEqual(self.roster.lookup(ALICE).roll, "IEC 2026 025")

    def test_muop(self):
        self.assertFalse(self.roster.is_muop(self.roster.lookup(ALICE)))
        self.assertTrue(self.roster.is_muop(self.roster.lookup(BOB_A)))
        self.assertEqual(self.roster.muop_report(), {"iec2026039@iiita.ac.in": [BOB_A, BOB_B]})

    def test_missing_file(self):
        with self.assertRaises(RosterError):
            load_roster(Path(self.tmp.name) / "nope.csv", "IEC-2026-LAB")

    def test_bad_headers(self):
        bad = Path(self.tmp.name) / "bad.csv"
        bad.write_text("a,b,c\n1,2,3\n")
        with self.assertRaises(RosterError):
            load_roster(bad, "IEC-2026-LAB")


class RealExportTests(unittest.TestCase):
    """The shape the actual Google Form export arrives in."""

    HEADER = "Timestamp,Email Address,msL-key,Full Name (as registered in ERP),Class_ID"
    BODY = [
        "8/20/2026 11:00:09,pmr2025001@iiita.ac.in,f76b4844-25c3-4aa4-a7bb-188f661cba7d,Shubhadeep Sarkar,PSP-LAB-SEC-D",
        "8/20/2026 11:39:55,pmm2025002@iiita.ac.in,eeaa3f6b-3170-49c5-a8fd-6aa7544248d0,Test anjul,PSP-LAB-SEC-D",
        "8/20/2026 11:40:14,pmm2025002@iiita.ac.in,invalid,Test Invalid,PSP-LAB-SEC-D",
        "8/21/2026 20:22:49,pmr2025001@iiita.ac.in,ba481b95-d145-4e48-b779-5ab97118b3b1,MultiUser sameEmail,PSP-LAB-SEC-D",
    ]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "responses.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, lines):
        # CRLF, as the real download uses
        self.path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
        return load_roster(self.path, "PSP-LAB-SEC-D")

    def test_renamed_columns_are_matched(self):
        """UUID became msL-key and CID became Class_ID."""
        roster = self.write([self.HEADER] + self.BODY)
        student = roster.lookup("f76b4844-25c3-4aa4-a7bb-188f661cba7d")
        self.assertIsNotNone(student)
        self.assertEqual(student.name, "Shubhadeep Sarkar")
        self.assertEqual(student.roll, "PMR 2025 001")

    def test_junk_uuid_is_rejected_not_loaded_as_a_student(self):
        roster = self.write([self.HEADER] + self.BODY)
        self.assertEqual(len(roster), 3)
        self.assertEqual(len(roster.rejected), 1)
        self.assertIn("not a valid UUID", roster.rejected[0].reason)
        self.assertEqual(roster.rejected[0].line, 4)

    def test_junk_uuid_does_not_trigger_a_false_muop(self):
        """The 'invalid' row shares an email with a real one. Counting it would
        wrongly flag that student as having two devices."""
        roster = self.write([self.HEADER] + self.BODY)
        anjul = roster.lookup("eeaa3f6b-3170-49c5-a8fd-6aa7544248d0")
        self.assertFalse(roster.is_muop(anjul))
        self.assertEqual(list(roster.muop_report()), ["pmr2025001@iiita.ac.in"])

    def test_genuine_muop_still_fires(self):
        roster = self.write([self.HEADER] + self.BODY)
        student = roster.lookup("ba481b95-d145-4e48-b779-5ab97118b3b1")
        self.assertTrue(roster.is_muop(student))

    def test_stale_header_row_above_the_real_one(self):
        """A hand-written label row that lists the columns in a DIFFERENT order.
        Trusting it loads names as UUIDs and everyone fails verification."""
        stale = "Timestamp,Email Address (IIITA),Name,UUID,CID"
        roster = self.write([stale, self.HEADER] + self.BODY)
        self.assertEqual(len(roster), 3)
        student = roster.lookup("f76b4844-25c3-4aa4-a7bb-188f661cba7d")
        self.assertIsNotNone(student, "header detection picked the wrong row")
        self.assertEqual(student.name, "Shubhadeep Sarkar")

    def test_lookup_normalises_the_scanned_uuid(self):
        roster = self.write([self.HEADER] + self.BODY)
        for variant in ("F76B4844-25C3-4AA4-A7BB-188F661CBA7D",
                        "f76b484425c34aa4a7bb188f661cba7d",
                        "  f76b4844-25c3-4aa4-a7bb-188f661cba7d  "):
            self.assertIsNotNone(roster.lookup(variant), variant)

    def test_blank_lines_are_skipped(self):
        roster = self.write([self.HEADER, self.BODY[0], "", self.BODY[1]])
        self.assertEqual(len(roster), 2)

    def test_no_header_at_all_is_an_error(self):
        with self.assertRaises(RosterError):
            self.write(self.BODY)


class TimestampTests(unittest.TestCase):
    def test_both_formats_in_one_file_parse(self):
        self.assertEqual(roster_mod.parse_timestamp("8/20/2026 11:00:09"),
                         datetime(2026, 8, 20, 11, 0, 9))
        self.assertEqual(roster_mod.parse_timestamp("2026-08-20 09:01:00"),
                         datetime(2026, 8, 20, 9, 1, 0))

    def test_unparseable_is_none_never_an_exception(self):
        for junk in ("", "   ", "garbage", "13/45/9999", None):
            self.assertIsNone(roster_mod.parse_timestamp(junk))

    def test_date_order_inferred_from_an_unambiguous_row(self):
        self.assertEqual(roster_mod._infer_date_order(["8/20/2026 11:00:09"]), roster_mod.MONTH_FIRST)
        self.assertEqual(roster_mod._infer_date_order(["20/8/2026 11:00:09"]), roster_mod.DAY_FIRST)

    def test_date_order_says_so_when_it_cannot_tell(self):
        self.assertEqual(roster_mod._infer_date_order(["9/8/2026 10:00:00"]), roster_mod.AMBIGUOUS)
        self.assertEqual(roster_mod._infer_date_order(["2026-08-20 09:01:00"]), roster_mod.AMBIGUOUS)

    def test_inferred_order_changes_the_parse(self):
        self.assertEqual(roster_mod.parse_timestamp("9/8/2026 10:00:00", roster_mod.DAY_FIRST),
                         datetime(2026, 8, 9, 10, 0, 0))
        self.assertEqual(roster_mod.parse_timestamp("9/8/2026 10:00:00", roster_mod.MONTH_FIRST),
                         datetime(2026, 9, 8, 10, 0, 0))

    def test_latest_registration_wins_regardless_of_file_order(self):
        """Rows are not sorted in the export, so ordering must come from the stamp."""
        device = "f76b4844-25c3-4aa4-a7bb-188f661cba7d"
        rows = [
            Student(uuid=device, email="a@x.ac.in", name="Old", cid="C",
                    registered_at=datetime(2026, 8, 20, 9, 0)),
        ]
        newer = Student(uuid=device, email="a@x.ac.in", name="New", cid="C",
                        registered_at=datetime(2026, 8, 21, 9, 0))
        self.assertEqual(roster_mod.Roster([newer] + rows, "C").lookup(device).name, "New")
        self.assertEqual(roster_mod.Roster(rows + [newer], "C").lookup(device).name, "New")


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.cfg = Config(
            pc_secret=PC_SECRET,
            app_secret=APP_SECRET,
            session=Session("IEC-2026-LAB", "2026-08-21", "A"),
            delta_t_max_seconds=90,
            clock_skew_tolerance_seconds=15,
            base_dir=base,
        )
        self.roster = load_roster(write_roster(base), "IEC-2026-LAB")
        self.verifier = Verifier(self.cfg, self.roster)
        self.now = int(time.time())
        self.source, _ = make_source_qr(PC_SECRET, self.cfg.session.c_id, gen_t=self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def respond(self, device_uuid, cap_t):
        return make_response_qr(APP_SECRET, self.source, device_uuid, cap_t)

    def test_pass(self):
        result = self.verifier.verify(self.respond(ALICE, self.now + 5))
        self.assertEqual(result.verdict, verify_mod.OK)
        self.assertEqual((result.title, result.subtitle), ("IEC 2026 025", "ALICE K"))
        self.assertTrue(result.ok and result.recordable)

    def test_user_not_found(self):
        result = self.verifier.verify(self.respond(GHOST, self.now + 5))
        self.assertEqual(result.verdict, verify_mod.UNF)
        self.assertEqual(result.title, "USER NOT FOUND")
        self.assertFalse(result.recordable)

    def test_muop_outranks_timeout(self):
        """A second device is a report, not a retry -- it must not surface as TO."""
        result = self.verifier.verify(self.respond(BOB_A, self.now + 9999))
        self.assertEqual(result.verdict, verify_mod.MUOP)

    def test_timeout(self):
        result = self.verifier.verify(self.respond(ALICE, self.now + 91))
        self.assertEqual(result.verdict, verify_mod.TO)
        self.assertTrue(result.recordable)

    def test_boundary_is_inclusive(self):
        self.assertEqual(self.verifier.verify(self.respond(ALICE, self.now + 90)).verdict, verify_mod.OK)

    def test_clock_skew_tolerated_then_rejected(self):
        self.assertEqual(self.verifier.verify(self.respond(ALICE, self.now - 10)).verdict, verify_mod.OK)
        self.assertEqual(self.verifier.verify(self.respond(ALICE, self.now - 60)).verdict, verify_mod.TO)

    def test_wrong_session(self):
        other, _ = make_source_qr(PC_SECRET, derive_cid("IEC-2026-LAB", "2026-08-20", "A"), gen_t=self.now)
        result = self.verifier.verify(make_response_qr(APP_SECRET, other, ALICE, self.now + 5))
        self.assertEqual(result.verdict, verify_mod.WRONG_SESSION)
        self.assertFalse(result.recordable)

    def test_forged_payload_unreadable(self):
        forged = make_response_qr(b"\xaa" * 32, self.source, ALICE, self.now + 5)
        self.assertEqual(self.verifier.verify(forged).verdict, verify_mod.UNREADABLE)

    def test_garbage_unreadable(self):
        for junk in ("", "hello world", "AAAA", "!!!!", "A" * 400):
            self.assertEqual(self.verifier.verify(junk).verdict, verify_mod.UNREADABLE)

    def test_every_verdict_has_a_colour_and_badge(self):
        for verdict in verify_mod.COLORS:
            result = verify_mod.Result(verdict, verify_mod._NULL_RELAY, "IEC-2026-LAB")
            self.assertEqual(len(result.color), 3)
            self.assertTrue(result.badge)


PREFILL = (
    "https://docs.google.com/forms/d/e/1FAIpQLSfcrN2FwVpHrylwadlAgEZNMPBbI3vfcaslzdxPU1mIDmD_EQ"
    "/viewform?usp=pp_url&entry.954365518=UUID&entry.1848492482=NAME&entry.1848492483=CID"
)
FORM_ID = "1FAIpQLSfcrN2FwVpHrylwadlAgEZNMPBbI3vfcaslzdxPU1mIDmD_EQ"
PWA = "https://luciuspertis.github.io/mattend-0/"


def a_class(**overrides):
    fields = dict(key="psp-lab-d", course_cid="PSP-LAB-SEC-D", label="PSP Lab Sec D",
                  form_id=FORM_ID, entries={"uuid": "954365518", "name": "1848492482",
                                            "cid": "1848492483"})
    fields.update(overrides)
    return classroom_mod.Classroom(**fields)


class PrefilledLinkTests(unittest.TestCase):
    """Google Forms never shows entry ids, so they come out of a prefilled link."""

    def test_extracts_form_id_and_every_entry(self):
        form_id, entries = classroom_mod.parse_prefilled_link(PREFILL)
        self.assertEqual(form_id, FORM_ID)
        self.assertEqual(entries, {"954365518": "UUID", "1848492482": "NAME", "1848492483": "CID"})

    def test_roles_guessed_from_the_sample_values(self):
        _form_id, entries = classroom_mod.parse_prefilled_link(PREFILL)
        self.assertEqual(classroom_mod.guess_roles(entries),
                         {"uuid": "954365518", "name": "1848492482", "cid": "1848492483"})

    def test_role_hints_are_forgiving(self):
        self.assertEqual(
            classroom_mod.guess_roles({"1": "msL-key", "2": "Full Name", "3": "Section"}),
            {"uuid": "1", "name": "2", "cid": "3"},
        )

    def test_values_are_url_decoded(self):
        _f, entries = classroom_mod.parse_prefilled_link(
            "https://docs.google.com/forms/d/e/" + FORM_ID + "/viewform?entry.7=Full+Name%21")
        self.assertEqual(entries["7"], "Full Name!")

    def test_short_form_url_shape(self):
        form_id, _entries = classroom_mod.parse_prefilled_link(
            f"https://docs.google.com/forms/d/{FORM_ID}/viewform?entry.7=UUID")
        self.assertEqual(form_id, FORM_ID)

    def test_useful_errors(self):
        for bad, fragment in ((""    , "paste"),
                              ("https://example.com/nope", "Google Form"),
                              (f"https://docs.google.com/forms/d/e/{FORM_ID}/viewform", "entry")):
            with self.assertRaises(classroom_mod.ClassroomError) as caught:
                classroom_mod.parse_prefilled_link(bad)
            self.assertIn(fragment, str(caught.exception))


class EnrollUrlTests(unittest.TestCase):
    def test_round_trip(self):
        url = enroll_mod.build_url(a_class(), PWA)
        parsed = enroll_mod.parse_url(url)
        self.assertEqual(parsed["course_cid"], "PSP-LAB-SEC-D")
        self.assertEqual(parsed["form_id"], FORM_ID)
        self.assertEqual(parsed["entry_uuid"], "954365518")
        self.assertEqual(parsed["entry_name"], "1848492482")
        self.assertEqual(parsed["entry_cid"], "1848492483")

    def test_recognised_as_enrollment(self):
        self.assertTrue(enroll_mod.looks_like_enrollment(enroll_mod.build_url(a_class(), PWA)))

    def test_a_source_qr_is_not_mistaken_for_enrollment(self):
        """The PWA routes on this, so a false positive would break attendance."""
        source, _ = make_source_qr(PC_SECRET, 0x1234)
        for text in (source, "", "hello", "https://example.com/", "https://x.test/?e=2"):
            self.assertFalse(enroll_mod.looks_like_enrollment(text), text)

    def test_cid_entry_is_optional(self):
        url = enroll_mod.build_url(a_class(entries={"uuid": "1", "name": "2"}), PWA)
        self.assertEqual(enroll_mod.parse_url(url)["entry_cid"], "")

    def test_prefill_url_carries_the_students_details(self):
        parsed = enroll_mod.parse_url(enroll_mod.build_url(a_class(), PWA))
        url = enroll_mod.prefill_url(parsed, "abc-123", "Test Student")
        self.assertIn(FORM_ID, url)
        self.assertIn("entry.954365518=abc-123", url)
        self.assertIn("entry.1848492482=Test+Student", url)
        self.assertIn("entry.1848492483=PSP-LAB-SEC-D", url)

    def test_incomplete_class_refuses_to_build(self):
        for room, why in ((a_class(form_id=""), "Form"),
                          (a_class(entries={"name": "2"}), "device"),
                          (a_class(entries={"uuid": "1"}), "name")):
            with self.assertRaises(classroom_mod.ClassroomError) as caught:
                enroll_mod.build_url(room, PWA)
            self.assertIn(why, str(caught.exception))

    def test_missing_pwa_url_is_a_clear_error(self):
        with self.assertRaises(classroom_mod.ClassroomError):
            enroll_mod.build_url(a_class(), "")

    def test_qr_stays_scannable_from_a_printout(self):
        import qrcode

        url = enroll_mod.build_url(a_class(label="A Fairly Long Class Name Here"), PWA)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        self.assertLessEqual(qr.version, 12, f"{len(url)} chars -> v{qr.version}")


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "classes.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_reload(self):
        registry = classroom_mod.Registry(self.path, {})
        registry.add(a_class())
        registry.save()
        again = classroom_mod.Registry.load(self.path)
        self.assertEqual(len(again), 1)
        self.assertEqual(again.active.course_cid, "PSP-LAB-SEC-D")
        self.assertEqual(again.active.entries["uuid"], "954365518")

    def test_keys_never_collide(self):
        registry = classroom_mod.Registry(self.path, {})
        first = registry.add(a_class(key="lab"))
        second = registry.add(a_class(key="lab"))
        self.assertNotEqual(first.key, second.key)

    def test_rename_keeps_active_pointing_at_it(self):
        registry = classroom_mod.Registry(self.path, {})
        room = registry.add(a_class(key="new-class"))
        registry.set_active(room.key)
        new_key = registry.rename("new-class", "PSP-LAB-SEC-D")
        self.assertEqual(new_key, "psp-lab-sec-d")
        self.assertEqual(registry.active.key, new_key)

    def test_removing_the_active_class_picks_another(self):
        registry = classroom_mod.Registry(self.path, {})
        registry.add(a_class(key="one"))
        registry.add(a_class(key="two"))
        registry.set_active("one")
        registry.remove("one")
        self.assertIsNotNone(registry.active)
        self.assertEqual(registry.active.key, "two")

    def test_date_today_resolves_without_editing(self):
        from datetime import date

        room = a_class()
        self.assertEqual(room.date, classroom_mod.TODAY)
        self.assertEqual(room.resolved_date, date.today().isoformat())

    def test_c_id_separates_slots_and_courses(self):
        base = a_class().c_id
        self.assertNotEqual(base, a_class(slot="B").c_id)
        self.assertNotEqual(base, a_class(course_cid="OTHER").c_id)

    def test_migrates_a_pre_registry_config(self):
        config_path = Path(self.tmp.name) / "config.json"
        config_path.write_text(json.dumps({
            "session": {"course_cid": "PSP-LAB-SEC-D", "label": "PSP Lab", "slot": "C"},
            "roster_csv": "data/responses.csv", "delta_t_max_seconds": 8,
        }))
        registry = classroom_mod.Registry(self.path, {})
        room = classroom_mod.migrate_from_config(registry, config_path)
        self.assertIsNotNone(room)
        self.assertEqual(room.course_cid, "PSP-LAB-SEC-D")
        self.assertEqual(room.slot, "C")
        self.assertEqual(room.delta_t_max_seconds, 8)
        self.assertEqual(room.date, classroom_mod.TODAY, "the date should stop being a daily chore")

    def test_migration_does_not_clobber_existing_classes(self):
        config_path = Path(self.tmp.name) / "config.json"
        config_path.write_text(json.dumps({"session": {"course_cid": "X"}}))
        registry = classroom_mod.Registry(self.path, {})
        registry.add(a_class())
        self.assertIsNone(classroom_mod.migrate_from_config(registry, config_path))
        self.assertEqual(len(registry), 1)


class SignatureTests(unittest.TestCase):
    """Device keys: knowing the app secret is no longer enough to be somebody."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.pub, self.private = keys_mod.generate_device_key()
        self.other_pub, self.other_private = keys_mod.generate_device_key()
        self.now = int(time.time())

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, rows, **cfg_overrides):
        path = self.base / "responses.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Timestamp", "Email Address", "msL-key", "msL-pub",
                             "Full Name", "Class_ID"])
            writer.writerows(rows)
        roster = load_roster(path, "IEC-2026-LAB")
        settings = dict(delta_t_max_seconds=90, submit_window_seconds=10,
                        capture_window_seconds=120, clock_skew_tolerance_seconds=15)
        settings.update(cfg_overrides)
        cfg = Config(pc_secret=PC_SECRET, app_secret=APP_SECRET,
                     session=Session("IEC-2026-LAB", "2026-08-21", "A"),
                     base_dir=self.base, **settings)
        self.ring = keys_mod.KeyRing.fixed(PC_SECRET)
        self.source, _ = make_source_qr(PC_SECRET, cfg.session.c_id, gen_t=self.now)
        return Verifier(cfg, roster, pc_keys=self.ring)

    def signed_rows(self, pubkey=None):
        return [("8/20/2026 09:01", "iec2026025@iiita.ac.in", ALICE,
                 self.pub if pubkey is None else pubkey, "Alice K", "IEC-2026-LAB")]

    def respond(self, private=None, cap_offset=2, sub_offset=2):
        return make_response_qr(APP_SECRET, self.source, ALICE,
                                self.now + cap_offset, self.now + sub_offset, private)

    def test_roster_reads_the_public_key_column(self):
        verifier = self.build(self.signed_rows())
        self.assertEqual(verifier.roster.lookup(ALICE).pubkey, self.pub)
        self.assertEqual(verifier.roster.signed_devices, 1)

    def test_correctly_signed_reply_passes(self):
        verifier = self.build(self.signed_rows())
        self.assertEqual(verifier.verify(self.respond(self.private), now=self.now + 3).verdict,
                         verify_mod.OK)

    def test_another_devices_signature_is_caught(self):
        """Somebody with the public app secret minting a reply as Alice."""
        verifier = self.build(self.signed_rows())
        result = verifier.verify(self.respond(self.other_private), now=self.now + 3)
        self.assertEqual(result.verdict, verify_mod.BAD_SIG)
        self.assertEqual(result.subtitle, "ALICE K")

    def test_unsigned_reply_from_a_registered_device_is_caught(self):
        verifier = self.build(self.signed_rows())
        result = verifier.verify(self.respond(None), now=self.now + 3)
        self.assertEqual(result.verdict, verify_mod.BAD_SIG)
        self.assertIn("unsigned", result.detail)

    def test_students_registered_before_signing_still_work(self):
        verifier = self.build(self.signed_rows(pubkey=""))
        self.assertEqual(verifier.verify(self.respond(None), now=self.now + 3).verdict,
                         verify_mod.OK)

    def test_require_signature_locks_out_the_unregistered(self):
        verifier = self.build(self.signed_rows(pubkey=""), require_signature=True)
        result = verifier.verify(self.respond(None), now=self.now + 3)
        self.assertEqual(result.verdict, verify_mod.BAD_SIG)
        self.assertIn("re-enrol", result.detail)

    def test_a_forged_signature_never_verifies(self):
        verifier = self.build(self.signed_rows())
        good = self.respond(self.private)
        # flip a bit inside the signed region and re-encrypt
        raw = crypto.unpack(APP_SECRET, crypto.DOMAIN_MOBILE, codec.b32decode(good))
        tampered = bytearray(raw)
        tampered[codec.SIGNED_LEN - 1] ^= 0x01
        forged = codec.b32encode(crypto.pack(APP_SECRET, crypto.DOMAIN_MOBILE, bytes(tampered)))
        self.assertEqual(verifier.verify(forged, now=self.now + 3).verdict, verify_mod.BAD_SIG)

    def test_bad_signature_outranks_muop(self):
        """A signature failure means the UUID is not evidence of who they are,
        so nothing downstream of it can be trusted."""
        rows = self.signed_rows() + [
            ("8/20/2026 09:02", "iec2026025@iiita.ac.in", BOB_A, self.pub, "Alice K", "IEC-2026-LAB")]
        verifier = self.build(rows)
        self.assertTrue(verifier.roster.is_muop(verifier.roster.lookup(ALICE)))
        self.assertEqual(verifier.verify(self.respond(self.other_private), now=self.now + 3).verdict,
                         verify_mod.BAD_SIG)


class ThreeClockTests(unittest.TestCase):
    """Gen_T, Cap_T and Sub_T each close a different hole."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.cfg = Config(pc_secret=PC_SECRET, app_secret=APP_SECRET,
                          session=Session("IEC-2026-LAB", "2026-08-21", "A"),
                          delta_t_max_seconds=8, submit_window_seconds=10,
                          capture_window_seconds=120, clock_skew_tolerance_seconds=5,
                          base_dir=base)
        self.roster = load_roster(write_roster(base), "IEC-2026-LAB")
        self.ring = keys_mod.KeyRing.fixed(PC_SECRET)
        self.verifier = Verifier(self.cfg, self.roster, pc_keys=self.ring)
        self.now = int(time.time())
        self.source, _ = make_source_qr(PC_SECRET, self.cfg.session.c_id, gen_t=self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def respond(self, cap, sub):
        return make_response_qr(APP_SECRET, self.source, ALICE, cap, sub)

    def test_live_phone_passes(self):
        result = self.verifier.verify(self.respond(self.now + 2, self.now + 30), now=self.now + 31)
        self.assertEqual(result.verdict, verify_mod.OK)

    def test_photographed_screen_caught_by_delta_t(self):
        result = self.verifier.verify(self.respond(self.now + 60, self.now + 60), now=self.now + 61)
        self.assertEqual(result.verdict, verify_mod.TO)
        self.assertIn("stale source QR", result.detail)

    def test_screenshotted_reply_caught_by_sub_t(self):
        """A frozen image cannot refresh Sub_T; a live phone re-renders."""
        result = self.verifier.verify(self.respond(self.now + 2, self.now + 2), now=self.now + 45)
        self.assertEqual(result.verdict, verify_mod.TO)
        self.assertIn("not a live screen", result.detail)

    def test_whole_journey_is_bounded_even_if_the_phone_keeps_refreshing(self):
        """Sub_T alone is client-controlled, so a phone could refresh forever."""
        late = self.now + 400
        result = self.verifier.verify(self.respond(self.now + 2, late), now=late)
        self.assertEqual(result.verdict, verify_mod.TO)
        self.assertIn("since capture", result.detail)

    def test_clock_skew_is_tolerated(self):
        result = self.verifier.verify(self.respond(self.now + 2, self.now + 8), now=self.now + 3)
        self.assertEqual(result.verdict, verify_mod.OK)


class KeyRotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.cfg = Config(pc_secret=PC_SECRET, app_secret=APP_SECRET,
                          session=Session("IEC-2026-LAB", "2026-08-21", "A"),
                          delta_t_max_seconds=90, submit_window_seconds=60,
                          capture_window_seconds=600, clock_skew_tolerance_seconds=15,
                          base_dir=base)
        self.roster = load_roster(write_roster(base), "IEC-2026-LAB")
        self.ring = keys_mod.KeyRing.ephemeral()
        self.verifier = Verifier(self.cfg, self.roster, pc_keys=self.ring)
        self.now = int(time.time())

    def tearDown(self):
        self.tmp.cleanup()

    def reply(self):
        source, _ = make_source_qr(self.ring.current, self.cfg.session.c_id, gen_t=self.now)
        return make_response_qr(APP_SECRET, source, ALICE, self.now + 2, self.now + 2)

    def test_each_launch_gets_a_different_key(self):
        self.assertNotEqual(keys_mod.KeyRing.ephemeral().current,
                            keys_mod.KeyRing.ephemeral().current)

    def test_reply_from_before_a_rotation_says_try_again(self):
        payload = self.reply()
        self.assertEqual(self.verifier.verify(payload, now=self.now + 3).verdict, verify_mod.OK)
        self.ring.rotate()
        result = self.verifier.verify(payload, now=self.now + 3)
        self.assertEqual(result.verdict, verify_mod.TO)
        self.assertIn("key reset", result.detail)

    def test_two_rotations_later_it_is_simply_unreadable(self):
        payload = self.reply()
        self.ring.rotate()
        self.ring.rotate()
        self.assertEqual(self.verifier.verify(payload, now=self.now + 3).verdict,
                         verify_mod.UNREADABLE)

    def test_a_fresh_reply_after_rotation_passes(self):
        self.ring.rotate()
        self.assertEqual(self.verifier.verify(self.reply(), now=self.now + 3).verdict,
                         verify_mod.OK)


class LedgerTests(unittest.TestCase):
    """Rescan rules. See ledger.py -- three cases, not one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.cfg = Config(
            pc_secret=PC_SECRET, app_secret=APP_SECRET,
            session=Session("IEC-2026-LAB", "2026-08-21", "A"),
            delta_t_max_seconds=90, clock_skew_tolerance_seconds=15, base_dir=base,
        )
        self.roster = load_roster(write_roster(base), "IEC-2026-LAB")
        self.verifier = Verifier(self.cfg, self.roster)
        self.now = int(time.time())
        self.source, _ = make_source_qr(PC_SECRET, self.cfg.session.c_id, gen_t=self.now)
        self.ledger = ledger_mod.ScanLedger(cooldown_seconds=3.0)

    def tearDown(self):
        self.tmp.cleanup()

    def verdict(self, device_uuid, cap_t):
        return self.verifier.verify(make_response_qr(APP_SECRET, self.source, device_uuid, cap_t))

    def test_same_phone_still_in_frame_stays_silent(self):
        good = self.verdict(ALICE, self.now + 2)
        self.assertEqual(self.ledger.submit(ALICE, good, now=100.0), ledger_mod.SHOW)
        for tick in (100.1, 101.0, 102.9):
            self.assertEqual(self.ledger.submit(ALICE, good, now=tick), ledger_mod.SILENT)

    def test_rescan_after_passing_reports_instead_of_vanishing(self):
        good = self.verdict(ALICE, self.now + 2)
        self.ledger.submit(ALICE, good, now=100.0)
        self.assertEqual(self.ledger.submit(ALICE, good, now=200.0), ledger_mod.REPEAT)
        self.assertEqual(self.ledger.repeats, 1)

    def test_a_pass_is_never_downgraded_by_a_later_scan(self):
        self.ledger.submit(ALICE, self.verdict(ALICE, self.now + 2), now=100.0)
        self.ledger.submit(ALICE, self.verdict(ALICE, self.now + 9999), now=200.0)
        self.assertEqual(self.ledger.record_for(ALICE).verdict, verify_mod.OK)
        self.assertEqual(self.ledger.present, 1)

    def test_a_timed_out_student_can_actually_retry(self):
        """The TIMEOUT card says 'try again'. Blocking the retry made that a lie."""
        late = self.verdict(ALICE, self.now + 500)
        self.assertEqual(late.verdict, verify_mod.TO)
        self.assertEqual(self.ledger.submit(ALICE, late, now=100.0), ledger_mod.SHOW)
        self.assertEqual(self.ledger.present, 0)
        self.assertEqual(self.ledger.flagged, 1)

        retry = self.verdict(ALICE, self.now + 2)
        self.assertEqual(self.ledger.submit(ALICE, retry, now=200.0), ledger_mod.SHOW)
        self.assertEqual(self.ledger.present, 1)
        self.assertEqual(self.ledger.flagged, 0, "the timeout should no longer count against them")

    def test_an_unfound_student_can_retry_after_registering(self):
        ghost = self.verdict(GHOST, self.now + 2)
        self.assertEqual(self.ledger.submit(GHOST, ghost, now=100.0), ledger_mod.SHOW)
        self.assertEqual(self.ledger.submit(GHOST, ghost, now=200.0), ledger_mod.SHOW)

    def test_tallies_never_double_count(self):
        for tick in (100.0, 200.0, 300.0, 400.0):
            self.ledger.submit(ALICE, self.verdict(ALICE, self.now + 2), now=tick)
        self.assertEqual(self.ledger.present, 1)
        self.assertEqual(self.ledger.devices, 1)
        self.assertEqual(self.ledger.count(ALICE), 4)
        self.assertEqual(self.ledger.repeats, 3)

    def test_separate_devices_are_independent(self):
        self.ledger.submit(ALICE, self.verdict(ALICE, self.now + 2), now=100.0)
        self.ledger.submit(BOB_A, self.verdict(BOB_A, self.now + 2), now=100.0)
        self.assertEqual(self.ledger.devices, 2)
        self.assertEqual(self.ledger.present, 1)   # BOB_A is MUoP, not a pass
        self.assertEqual(self.ledger.flagged, 1)

    def test_clear_lets_everyone_scan_again(self):
        self.ledger.submit(ALICE, self.verdict(ALICE, self.now + 2), now=100.0)
        self.ledger.clear()
        self.assertEqual(self.ledger.submit(ALICE, self.verdict(ALICE, self.now + 2), now=101.0),
                         ledger_mod.SHOW)
        self.assertEqual(self.ledger.repeats, 0)


class PreviewTests(unittest.TestCase):
    """The aiming thumbnail is mirrored; the decode path is not."""

    def setUp(self):
        import numpy as np

        # asymmetric: bright block on the left third only
        self.frame = np.zeros((60, 120, 3), np.uint8)
        self.frame[:, :40] = 255

    def _decode(self, data: bytes):
        import cv2
        import numpy as np

        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)

    def test_mirrored_preview_flips_left_to_right(self):
        image = self._decode(camera_mod.preview_ppm(self.frame, 30, mirror=True))
        height, width = image.shape[:2]
        left = image[:, : width // 3].mean()
        right = image[:, -width // 3:].mean()
        self.assertGreater(right, left, "bright block should have moved to the right")

    def test_unmirrored_preview_keeps_the_side(self):
        image = self._decode(camera_mod.preview_ppm(self.frame, 30, mirror=False))
        height, width = image.shape[:2]
        self.assertGreater(image[:, : width // 3].mean(), image[:, -width // 3:].mean())

    def test_scales_to_the_requested_height(self):
        image = self._decode(camera_mod.preview_ppm(self.frame, 30))
        self.assertEqual(image.shape[0], 30)
        self.assertEqual(image.shape[1], 60)      # aspect preserved

    def test_degenerate_input_returns_none_not_a_crash(self):
        import numpy as np

        self.assertIsNone(camera_mod.preview_ppm(None, 30))
        self.assertIsNone(camera_mod.preview_ppm(self.frame, 0))
        self.assertIsNone(camera_mod.preview_ppm(np.zeros((0, 0, 3), np.uint8), 30))

    def test_worker_never_transforms_the_decode_frame(self):
        """Mirroring lives in the preview helper, not the capture loop."""
        import inspect

        source = inspect.getsource(camera_mod.CameraWorker._run)
        self.assertNotIn("flip", source)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.cfg = Config(
            pc_secret=PC_SECRET,
            app_secret=APP_SECRET,
            session=Session("IEC-2026-LAB", "2026-08-21", "A"),
            base_dir=base,
        )
        self.roster = load_roster(write_roster(base), "IEC-2026-LAB")
        self.verifier = Verifier(self.cfg, self.roster)
        self.store = Store(base / "dump.sqlite3")
        self.now = int(time.time())
        self.source, _ = make_source_qr(PC_SECRET, self.cfg.session.c_id, gen_t=self.now)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_rescan_updates_one_row(self):
        for offset in (2, 4, 6):
            self.store.record(self.verifier.verify(make_response_qr(APP_SECRET, self.source, ALICE, self.now + offset)))
        rows = self.store.all_rows("IEC-2026-LAB")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scan_count"], 3)
        self.assertEqual(rows[0]["cap_t"], self.now + 6)

    def test_pass_is_never_downgraded_by_a_later_timeout(self):
        self.store.record(self.verifier.verify(make_response_qr(APP_SECRET, self.source, ALICE, self.now + 2)))
        self.store.record(self.verifier.verify(make_response_qr(APP_SECRET, self.source, ALICE, self.now + 500)))
        self.assertEqual(self.store.all_rows()[0]["verdict"], verify_mod.OK)
        self.assertEqual(len(self.store.present("IEC-2026-LAB")), 1)

    def test_dump_carries_the_four_diagram_columns(self):
        self.store.record(self.verifier.verify(make_response_qr(APP_SECRET, self.source, ALICE, self.now + 2)))
        row = self.store.all_rows()[0]
        self.assertEqual(row["uuid"], ALICE)
        self.assertEqual(row["c_id"], self.cfg.session.c_id)
        self.assertEqual(row["gen_t"], self.now)
        self.assertEqual(row["cap_t"], self.now + 2)

    def test_export_csv(self):
        self.store.record(self.verifier.verify(make_response_qr(APP_SECRET, self.source, ALICE, self.now + 2)))
        out = self.store.export_csv(Path(self.tmp.name) / "out.csv", "IEC-2026-LAB")
        text = out.read_text()
        self.assertIn("IEC 2026 025", text)
        self.assertIn("Alice K", text)


if __name__ == "__main__":
    unittest.main()
