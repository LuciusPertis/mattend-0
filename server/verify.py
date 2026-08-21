"""The verification pipeline of the second diagram, top to bottom.

    Z*_s --P_s^-1--> UUID, Cap_T, Z*_{CID,GT} --P_c^-1--> C_ID, Gen_T
      |                 |                                    |
      |                 +--> roster lookup: UNF / MUoP       +--> C_ID sanity check
      +--> ΔT = Cap_T - Gen_T: TO

Precedence is deliberate. A payload that does not decrypt, or that carries
another room's C_ID, never reaches the roster -- there is no identity to trust
in it. After that, identity problems (UNF, MUoP) outrank the timing problem
(TO), because a timeout is a "try again" while MUoP is a report.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .protocol import BadPacket, BadPayload, Relay, open_response_qr
from .roster import Roster, Student

OK = "OK"
UNF = "UNF"
MUOP = "MUOP"
TO = "TO"
WRONG_SESSION = "WRONG_SESSION"
UNREADABLE = "UNREADABLE"

# RGB. The first four are the four cards drawn in the diagram.
COLORS = {
    OK: (34, 160, 74),             # green
    UNF: (130, 130, 130),          # grey
    MUOP: (200, 40, 40),           # red
    TO: (222, 170, 20),            # yellow
    WRONG_SESSION: (90, 90, 110),  # slate
    UNREADABLE: (70, 70, 70),      # near-black
}

_NULL_RELAY = Relay(device_uuid="", cap_t=0, c_id=0, gen_t=0)


@dataclass
class Result:
    verdict: str
    relay: Relay
    course_cid: str
    student: Student | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict == OK

    @property
    def recordable(self) -> bool:
        """Whether this belongs in the local dump -- i.e. we know who it was."""
        return self.verdict in (OK, MUOP, TO)

    @property
    def color(self) -> tuple[int, int, int]:
        return COLORS[self.verdict]

    @property
    def title(self) -> str:
        if self.student:
            return self.student.roll
        return "USER NOT FOUND" if self.verdict == UNF else "INVALID CODE"

    @property
    def subtitle(self) -> str:
        return self.student.name.upper() if self.student else self.detail

    @property
    def badge(self) -> str:
        return {
            OK: "PASS",
            UNF: "X",
            MUOP: "MUoP REPORTED",
            TO: "TIMEOUT - TRY AGAIN",
            WRONG_SESSION: "WRONG SESSION",
            UNREADABLE: "UNREADABLE",
        }[self.verdict]

    def line(self) -> str:
        parts = [self.verdict.ljust(13), self.title]
        if self.subtitle:
            parts.append(self.subtitle)
        if self.verdict in (OK, MUOP, TO):
            parts.append(f"dT={self.relay.delta_t}s")
        if self.detail and self.student:
            parts.append(f"({self.detail})")
        return " | ".join(parts)


class Verifier:
    def __init__(self, config: Config, roster: Roster):
        self.config = config
        self.roster = roster
        self.expected_c_id = config.session.c_id

    def verify(self, scanned_text: str) -> Result:
        course = self.config.session.course_cid

        try:
            relay = open_response_qr(self.config.pc_secret, self.config.app_secret, scanned_text)
        except (BadPacket, BadPayload, ValueError) as exc:
            return Result(UNREADABLE, _NULL_RELAY, course, detail=str(exc))

        if relay.c_id != self.expected_c_id:
            return Result(
                WRONG_SESSION,
                relay,
                course,
                detail=f"C_ID 0x{relay.c_id:08x} != 0x{self.expected_c_id:08x}",
            )

        student = self.roster.lookup(relay.device_uuid)
        if student is None:
            return Result(UNF, relay, course, detail=f"uuid {relay.device_uuid[:8]}… not registered for {course}")

        if self.roster.is_muop(student):
            devices = self.roster.devices_for(student.email)
            return Result(MUOP, relay, course, student=student, detail=f"{len(devices)} devices on {student.email}")

        delta_t = relay.delta_t
        if delta_t < -self.config.clock_skew_tolerance_seconds:
            return Result(TO, relay, course, student=student, detail=f"clock skew {delta_t}s")
        if delta_t > self.config.delta_t_max_seconds:
            return Result(TO, relay, course, student=student, detail=f"ΔT {delta_t}s > {self.config.delta_t_max_seconds}s")

        return Result(OK, relay, course, student=student, detail=f"ΔT {delta_t}s")
