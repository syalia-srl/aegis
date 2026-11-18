"""A phone running the aegis app, as far as the server can tell: a P-256 key,
the pair request, and a /device socket that answers calls from a dict of
handlers. The real one is android/; this one speaks the same frames."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Phone:
    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id: str | None = None

    @property
    def public_key(self) -> str:
        der = self.key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return b64e(der)

    def sign(self, nonce: bytes) -> str:
        return b64e(self.key.sign(nonce, ec.ECDSA(hashes.SHA256())))

    def hello(self, challenge: dict, ops=("device.info",), **extra) -> dict:
        nonce = b64d(challenge["nonce"])
        return {
            "t": "hello",
            "id": self.id,
            "sig": self.sign(nonce),
            "shell": 1,
            "api": 35,
            "model": "Fake Pixel",
            "ops": list(ops),
        } | extra
