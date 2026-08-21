"""python3 -m unittest server.test_server -v"""

from __future__ import annotations

import csv
import tempfile
import time
import unittest
from pathlib import Path

from . import codec, crypto, verify as verify_mod
from .config import Config, Session, derive_cid
from .protocol import make_response_qr, make_source_qr, open_response_qr
from .roster import RosterError, load as load_roster
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
        plaintext = codec.encode_outer(blob, ALICE, 1700000005)
        self.assertEqual(codec.decode_outer(plaintext), (blob, ALICE, 1700000005))

    def test_qr_size_budget(self):
        """The inner QR must stay at version 1 or the projector hop gets hard to read."""
        import qrcode

        source, _ = make_source_qr(PC_SECRET, 0xFFFFFFFF, gen_t=0xFFFFFFFF)
        response = make_response_qr(APP_SECRET, source, ALICE, 0xFFFFFFFF)
        for text, ceiling in ((source, 1), (response, 3)):
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
