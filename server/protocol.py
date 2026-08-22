"""The two-hop relay, end to end.

    hop 1   PC (out) -> phone     Z*_{CID,GT} = P_c( C_ID || Gen_T )
    hop 2   phone -> PC (in)      Z*_s        = P_s( ... || sig_device )

Three clocks matter, and each closes a different hole:

    Gen_T   the projector made the source QR
    Cap_T   the phone read it            Cap_T - Gen_T  : stops a photographed screen
    Sub_T   the phone rendered its reply now   - Sub_T  : stops a screenshotted reply
                                        now   - Cap_T  : bounds the whole journey

Sub_T is what separates a live phone from a static image. A phone re-renders and
re-signs every couple of seconds, so its Sub_T is always fresh; a screenshot
forwarded to an absent friend carries a frozen one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import codec, crypto
from .codec import BadPayload
from .crypto import BadPacket
from .keys import BadSignature, KeyRing, sign_p1363, verify_signature


@dataclass(frozen=True)
class Relay:
    """Everything PC (in) recovers from a single scanned QR."""

    device_uuid: str
    cap_t: int
    sub_t: int
    c_id: int
    gen_t: int
    signature: bytes
    signed_bytes: bytes
    key_generation: int = 0        # 0 = current P_c key, 1 = the previous one

    @property
    def delta_t(self) -> int:
        """Seconds between the source QR being generated and the phone reading it."""
        return self.cap_t - self.gen_t

    @property
    def is_signed(self) -> bool:
        return self.signature != bytes(codec.SIG_LEN)

    def age(self, now: int | None = None) -> int:
        """Seconds since the phone last rendered this QR."""
        return (int(time.time()) if now is None else now) - self.sub_t

    def journey(self, now: int | None = None) -> int:
        """Seconds since the phone read the source QR."""
        return (int(time.time()) if now is None else now) - self.cap_t

    def verify_device(self, public_key) -> None:
        """Raises BadSignature unless the registered device key signed this."""
        verify_signature(public_key, self.signed_bytes, self.signature)


def make_source_qr(pc_secret: bytes, c_id: int, gen_t: int | None = None) -> tuple[str, int]:
    """PC (out): build the payload for the projected QR. Returns (text, gen_t)."""
    gen_t = int(time.time()) if gen_t is None else gen_t
    blob = crypto.pack(pc_secret, crypto.DOMAIN_PC, codec.encode_inner(c_id, gen_t))
    return codec.b32encode(blob), gen_t


def make_response_qr(app_secret: bytes, source_text: str, device_uuid: str,
                     cap_t: int | None = None, sub_t: int | None = None,
                     device_private_key=None) -> str:
    """The phone's half. Lives here so tests and the simulator can exercise it.

    The real implementation of this hop is docs/protocol.js; the two must stay
    byte-for-byte identical.
    """
    cap_t = int(time.time()) if cap_t is None else cap_t
    sub_t = cap_t if sub_t is None else sub_t
    inner_blob = codec.b32decode(source_text)
    signed = codec.encode_signed(inner_blob, device_uuid, cap_t, sub_t)
    signature = sign_p1363(device_private_key, signed) if device_private_key else bytes(codec.SIG_LEN)
    plaintext = signed + signature
    return codec.b32encode(crypto.pack(app_secret, crypto.DOMAIN_MOBILE, plaintext))


def open_response_qr(pc_keys: KeyRing | bytes, app_secret: bytes, scanned_text: str) -> Relay:
    """PC (in): P_s^-1 then P_c^-1. Raises BadPayload / BadPacket on anything bogus.

    The inner blob is tried against the current P_c key first, then the previous
    generation, so a rotation turns a slow student's QR into "try again" rather
    than "invalid code".
    """
    outer_plain = crypto.unpack(app_secret, crypto.DOMAIN_MOBILE, codec.b32decode(scanned_text))
    inner_blob, device_uuid, cap_t, sub_t, signature, signed_bytes = codec.decode_outer(outer_plain)

    ring = pc_keys if isinstance(pc_keys, KeyRing) else KeyRing.fixed(pc_keys)
    last_error: Exception | None = None
    for generation, key in enumerate([ring.current, *ring.previous]):
        try:
            c_id, gen_t = codec.decode_inner(crypto.unpack(key, crypto.DOMAIN_PC, inner_blob))
        except (BadPacket, BadPayload) as exc:
            last_error = exc
            continue
        return Relay(device_uuid=device_uuid, cap_t=cap_t, sub_t=sub_t, c_id=c_id, gen_t=gen_t,
                     signature=signature, signed_bytes=signed_bytes, key_generation=generation)
    raise last_error or BadPacket("inner blob did not decrypt under any key generation")


__all__ = ["Relay", "make_source_qr", "make_response_qr", "open_response_qr",
           "BadPayload", "BadPacket", "BadSignature"]
