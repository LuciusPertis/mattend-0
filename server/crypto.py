"""P_c / P_s: the pack functions from the architecture diagram.

Both are the same construction under different keys and domain labels:

    P*(pt) = tag || (pt XOR keystream(tag))      tag = HMAC(k_mac, pt)[:6]

This is SIV mode -- the authentication tag doubles as the nonce -- which buys
authenticated encryption with only 6 bytes of overhead and no nonce to carry.
Overhead matters here: the inner QR has to stay at version 1 (21x21) so a phone
can read it off a projector from the back of the room.

Being deterministic is a feature, not a leak: the inner blob is a pure function
of (C_ID, Gen_T), so the same second always yields the same QR.

    P_c  -- domain "pc",     keyed by the lab secret. Never leaves the PCs.
    P_s  -- domain "mobile", keyed by the app-wide secret shared with the PWA.
"""

import hashlib
import hmac
from functools import lru_cache

TAG_LEN = 6

DOMAIN_PC = "pc"
DOMAIN_MOBILE = "mobile"


class BadPacket(ValueError):
    """Payload failed authentication, or was truncated//malformed."""


@lru_cache(maxsize=16)
def subkey(master: bytes, label: str) -> bytes:
    """Domain-separated 32-byte subkey from the 32-byte master secret.

    HMAC-SHA256 rather than something nicer because the phone half of this has
    to compute the identical value in WebCrypto, which offers HMAC and little
    else. See docs/protocol.js.
    """
    return hmac.new(master, label.encode(), hashlib.sha256).digest()


def _keystream(k_enc: bytes, tag: bytes, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hmac.new(k_enc, tag + bytes([counter]), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:n])


def _tag(k_mac: bytes, pt: bytes) -> bytes:
    return hmac.new(k_mac, pt, hashlib.sha256).digest()[:TAG_LEN]


def pack(master: bytes, domain: str, plaintext: bytes) -> bytes:
    """Encrypt-and-authenticate. Returns TAG_LEN + len(plaintext) bytes."""
    tag = _tag(subkey(master, domain + "/mac"), plaintext)
    stream = _keystream(subkey(master, domain + "/enc"), tag, len(plaintext))
    return tag + bytes(a ^ b for a, b in zip(plaintext, stream))


def unpack(master: bytes, domain: str, blob: bytes) -> bytes:
    """Inverse of pack. Raises BadPacket if the tag does not verify."""
    if len(blob) < TAG_LEN:
        raise BadPacket(f"packet too short: {len(blob)} bytes")
    tag, ciphertext = blob[:TAG_LEN], blob[TAG_LEN:]
    stream = _keystream(subkey(master, domain + "/enc"), tag, len(ciphertext))
    plaintext = bytes(a ^ b for a, b in zip(ciphertext, stream))
    if not hmac.compare_digest(_tag(subkey(master, domain + "/mac"), plaintext), tag):
        raise BadPacket("authentication tag mismatch")
    return plaintext
