"""Versioned AES-256-GCM envelope for exchange secrets at rest."""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialEncryptionError(Exception):
    pass


def _master_key() -> bytes:
    encoded = os.getenv("MASTER_KEY", "")
    try:
        key = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise CredentialEncryptionError("MASTER_KEY must be base64 encoding of exactly 32 random bytes") from exc
    if len(key) != 32:
        raise CredentialEncryptionError("MASTER_KEY must be base64 encoding of exactly 32 random bytes")
    return key


def encrypt_credential(plaintext: str, *, user_id: str, credential_id: str) -> str:
    nonce = os.urandom(12)
    associated_data = f"tradegen:exchange-credential:v1:{user_id}:{credential_id}".encode()
    ciphertext = AESGCM(_master_key()).encrypt(nonce, plaintext.encode("utf-8"), associated_data)
    return "v1." + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_credential(envelope: str, *, user_id: str, credential_id: str) -> str:
    if not envelope.startswith("v1."):
        raise CredentialEncryptionError("Unsupported credential envelope version")
    try:
        packed = base64.b64decode(envelope[3:].encode("ascii"), altchars=b"-_", validate=True)
        nonce, ciphertext = packed[:12], packed[12:]
        if len(nonce) != 12 or len(ciphertext) < 16:
            raise ValueError("Malformed envelope")
        associated_data = f"tradegen:exchange-credential:v1:{user_id}:{credential_id}".encode()
        return AESGCM(_master_key()).decrypt(nonce, ciphertext, associated_data).decode("utf-8")
    except Exception as exc:
        if isinstance(exc, CredentialEncryptionError):
            raise
        raise CredentialEncryptionError("Credential could not be authenticated or decrypted") from exc
