"""Two independent key stories.

**P_c rotation.** The station mints a fresh `pc_secret` every launch, held in
memory and never written to disk, and can rotate again on a keypress. Rotating
invalidates every source QR already on a phone -- which is the point: a photo of
the projector, or a blob forwarded to an absent friend, dies the instant the
teacher presses the key. One previous generation is kept so a student who is
merely slow gets "try again" instead of "invalid code".

Rotation applies to the merged station only. `pc_out` and `pc_in` on separate
machines cannot share an in-memory key, so they keep using the one in config.json.

**Device signatures.** Each phone generates an ECDSA P-256 keypair at enrollment
and submits the public half through the Google Form. The station verifies the
response QR against the key registered for that UUID, so knowing the app secret
is no longer enough to impersonate somebody -- and the app secret is public
JavaScript, so previously it was.

P-256 rather than Ed25519 purely for reach: WebCrypto has had P-256 for a
decade, while Ed25519 arrived in Chrome 137 and Firefox 130. Student phones are
exactly where old browsers live. Same 64-byte signature either way.
"""

from __future__ import annotations

import base64
import secrets
import time
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

SIG_LEN = 64            # P1363 r||s, what WebCrypto's ECDSA sign() returns
PUBKEY_LEN = 33         # X9.62 compressed point
KEEP_GENERATIONS = 2    # current + one previous


class BadSignature(ValueError):
    pass


# ---------------------------------------------------------------- device keys


def b64u_decode(text: str) -> bytes:
    text = (text or "").strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(text + "=" * (-len(text) % 4))


def b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def load_public_key(encoded: str):
    """Parse a base64url compressed P-256 point, as submitted on the form."""
    raw = b64u_decode(encoded)
    if len(raw) != PUBKEY_LEN:
        raise BadSignature(f"public key is {len(raw)} bytes, want {PUBKEY_LEN}")
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError as exc:
        raise BadSignature(f"not a valid P-256 point: {exc}") from exc


def verify_signature(public_key, message: bytes, signature: bytes) -> None:
    """Raises BadSignature unless `signature` is this key's over `message`."""
    if len(signature) != SIG_LEN:
        raise BadSignature(f"signature is {len(signature)} bytes, want {SIG_LEN}")
    if signature == bytes(SIG_LEN):
        raise BadSignature("empty signature")
    der = utils.encode_dss_signature(
        int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    )
    try:
        public_key.verify(der, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise BadSignature("signature does not match the registered device key") from exc


def generate_device_key() -> tuple[str, ec.EllipticCurvePrivateKey]:
    """Only used by tests and the simulator; real keys are made on the phone."""
    private = ec.generate_private_key(ec.SECP256R1())
    encoded = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint
    )
    return b64u_encode(encoded), private


def sign_p1363(private_key, message: bytes) -> bytes:
    r, s = utils.decode_dss_signature(private_key.sign(message, ec.ECDSA(hashes.SHA256())))
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


# ------------------------------------------------------------------ P_c ring


@dataclass
class KeyRing:
    """Current P_c key plus the previous one, newest first."""

    generation: int = 0
    rotated_at: float = field(default_factory=time.monotonic)
    _keys: list[bytes] = field(default_factory=list)

    @classmethod
    def ephemeral(cls) -> "KeyRing":
        ring = cls()
        ring._keys = [secrets.token_bytes(32)]
        return ring

    @classmethod
    def fixed(cls, secret: bytes) -> "KeyRing":
        """A ring that never rotates -- the persisted key, for two-machine setups."""
        ring = cls()
        ring._keys = [secret]
        return ring

    @property
    def current(self) -> bytes:
        return self._keys[0]

    @property
    def previous(self) -> list[bytes]:
        return self._keys[1:]

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.rotated_at

    def rotate(self) -> int:
        self._keys.insert(0, secrets.token_bytes(32))
        del self._keys[KEEP_GENERATIONS:]
        self.generation += 1
        self.rotated_at = time.monotonic()
        return self.generation
