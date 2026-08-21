"""The two-hop relay, end to end.

    hop 1   PC (out) -> phone     Z*_{CID,GT} = P_c( C_ID || Gen_T )
    hop 2   phone -> PC (in)      Z*_s        = P_s( Z*_{CID,GT} || UUID || Cap_T )

PC (in) runs both inverses: P_s^-1 to recover the device identity and capture
time, then P_c^-1 on the blob the phone relayed to recover where and when the
source QR was generated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import codec, crypto
from .codec import BadPayload
from .crypto import BadPacket


@dataclass(frozen=True)
class Relay:
    """Everything PC (in) recovers from a single scanned QR."""

    device_uuid: str
    cap_t: int
    c_id: int
    gen_t: int

    @property
    def delta_t(self) -> int:
        """Seconds between the source QR being generated and the phone reading it."""
        return self.cap_t - self.gen_t


def make_source_qr(pc_secret: bytes, c_id: int, gen_t: int | None = None) -> tuple[str, int]:
    """PC (out): build the payload for the projected QR. Returns (text, gen_t)."""
    gen_t = int(time.time()) if gen_t is None else gen_t
    blob = crypto.pack(pc_secret, crypto.DOMAIN_PC, codec.encode_inner(c_id, gen_t))
    return codec.b32encode(blob), gen_t


def make_response_qr(app_secret: bytes, source_text: str, device_uuid: str, cap_t: int | None = None) -> str:
    """The phone's half. Lives here so tests and the simulator can exercise it.

    The real implementation of this hop is docs/protocol.js; the two must
    stay byte-for-byte identical.
    """
    cap_t = int(time.time()) if cap_t is None else cap_t
    inner_blob = codec.b32decode(source_text)
    plaintext = codec.encode_outer(inner_blob, device_uuid, cap_t)
    return codec.b32encode(crypto.pack(app_secret, crypto.DOMAIN_MOBILE, plaintext))


def open_response_qr(pc_secret: bytes, app_secret: bytes, scanned_text: str) -> Relay:
    """PC (in): P_s^-1 then P_c^-1. Raises BadPayload / BadPacket on anything bogus."""
    outer_plain = crypto.unpack(app_secret, crypto.DOMAIN_MOBILE, codec.b32decode(scanned_text))
    inner_blob, device_uuid, cap_t = codec.decode_outer(outer_plain)
    c_id, gen_t = codec.decode_inner(crypto.unpack(pc_secret, crypto.DOMAIN_PC, inner_blob))
    return Relay(device_uuid=device_uuid, cap_t=cap_t, c_id=c_id, gen_t=gen_t)


__all__ = ["Relay", "make_source_qr", "make_response_qr", "open_response_qr", "BadPayload", "BadPacket"]
