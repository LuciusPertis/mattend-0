"""Wire encoding for the two QR payloads.

Everything is bit-packed and then Base32'd. Base32's alphabet (A-Z, 2-7) is a
subset of QR's alphanumeric mode, which encodes 5.5 bits per character instead
of the 8 bits per character that byte mode costs -- that difference is what
keeps the inner QR at version 1.

    inner   9 bytes plaintext ->  15 blob ->  24 chars  (QR v1-L holds 25)
    outer 104 bytes plaintext -> 110 blob -> 176 chars  (QR v6-L holds 195)

The outer QR grew from v3 to v6 when device signatures arrived: a P-256
signature is 64 bytes and cannot be truncated. At v6 a phone screen still gives
a 640x480 webcam about 5 pixels per module, which decodes comfortably.
"""

import base64
import struct
import uuid as uuidlib

PROTO_VERSION = 2

_INNER_STRUCT = struct.Struct(">BII")      # version, c_id, gen_t
_OUTER_HEAD = struct.Struct(">B")          # version
_OUTER_TAIL = struct.Struct(">II")         # cap_t, sub_t
INNER_BLOB_LEN = 15                        # crypto.TAG_LEN + _INNER_STRUCT.size
UUID_LEN = 16
SIG_LEN = 64                               # ECDSA P-256, P1363 r||s

# The part a phone signs: everything except the signature itself.
SIGNED_LEN = _OUTER_HEAD.size + INNER_BLOB_LEN + UUID_LEN + _OUTER_TAIL.size


class BadPayload(ValueError):
    """Payload was not decodable as a mattend QR."""


def b32encode(raw: bytes) -> str:
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def b32decode(text: str) -> bytes:
    text = text.strip().upper().replace(" ", "")
    padded = text + "=" * (-len(text) % 8)
    try:
        return base64.b32decode(padded, casefold=True)
    except Exception as exc:  # binascii.Error and friends
        raise BadPayload(f"not valid base32: {exc}") from exc


def encode_inner(c_id: int, gen_t: int) -> bytes:
    """Plaintext of Z*_{CID,GT}, pre-P_c."""
    return _INNER_STRUCT.pack(PROTO_VERSION, c_id & 0xFFFFFFFF, gen_t & 0xFFFFFFFF)


def decode_inner(plaintext: bytes) -> tuple[int, int]:
    """Returns (c_id, gen_t)."""
    if len(plaintext) != _INNER_STRUCT.size:
        raise BadPayload(f"inner payload is {len(plaintext)} bytes, want {_INNER_STRUCT.size}")
    version, c_id, gen_t = _INNER_STRUCT.unpack(plaintext)
    if version != PROTO_VERSION:
        raise BadPayload(f"unsupported inner protocol version {version}")
    return c_id, gen_t


def encode_signed(inner_blob: bytes, device_uuid: str, cap_t: int, sub_t: int) -> bytes:
    """The signed portion of Z*_s: everything the phone attests to."""
    if len(inner_blob) != INNER_BLOB_LEN:
        raise BadPayload(f"inner blob is {len(inner_blob)} bytes, want {INNER_BLOB_LEN}")
    raw_uuid = uuidlib.UUID(device_uuid).bytes
    return (
        _OUTER_HEAD.pack(PROTO_VERSION)
        + inner_blob
        + raw_uuid
        + _OUTER_TAIL.pack(cap_t & 0xFFFFFFFF, sub_t & 0xFFFFFFFF)
    )


def encode_outer(inner_blob: bytes, device_uuid: str, cap_t: int, sub_t: int,
                 signature: bytes = b"") -> bytes:
    """Plaintext of Z*_s, pre-P_s. An all-zero signature means "device unsigned"."""
    signature = signature or bytes(SIG_LEN)
    if len(signature) != SIG_LEN:
        raise BadPayload(f"signature is {len(signature)} bytes, want {SIG_LEN}")
    return encode_signed(inner_blob, device_uuid, cap_t, sub_t) + signature


def decode_outer(plaintext: bytes) -> tuple[bytes, str, int, int, bytes, bytes]:
    """Returns (inner_blob, device_uuid, cap_t, sub_t, signature, signed_bytes)."""
    expected = SIGNED_LEN + SIG_LEN
    if len(plaintext) != expected:
        raise BadPayload(f"outer payload is {len(plaintext)} bytes, want {expected}")
    (version,) = _OUTER_HEAD.unpack_from(plaintext, 0)
    if version != PROTO_VERSION:
        raise BadPayload(
            f"unsupported outer protocol version {version} -- the phone app is out of date"
        )
    offset = _OUTER_HEAD.size
    inner_blob = plaintext[offset:offset + INNER_BLOB_LEN]
    offset += INNER_BLOB_LEN
    device_uuid = str(uuidlib.UUID(bytes=plaintext[offset:offset + UUID_LEN]))
    offset += UUID_LEN
    cap_t, sub_t = _OUTER_TAIL.unpack_from(plaintext, offset)
    return (inner_blob, device_uuid, cap_t, sub_t,
            plaintext[SIGNED_LEN:], plaintext[:SIGNED_LEN])
