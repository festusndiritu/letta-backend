"""
Message encryption at rest.

Uses AES-256-GCM via the cryptography library.
A single server-side symmetric key encrypts all message content before
writing to the DB and decrypts on read.

This protects against raw DB dumps — an attacker with only the DB file
sees ciphertext. The key never touches the DB; it lives in the environment.

Key format: 32 random bytes, base64-encoded.
Generate: python -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"

NOT end-to-end encryption — the server decrypts to route messages.
That's the deliberate trade-off: server-side features (search, push content,
moderation hooks) require the server to see plaintext in memory briefly.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

_key: bytes | None = None


def _get_key() -> bytes:
    global _key
    if _key is None:
        raw = settings.message_encryption_key
        if not raw:
            raise RuntimeError(
                "MESSAGE_ENCRYPTION_KEY is not set. "
                "Generate one: python -c \"import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())\""
            )
        _key = base64.b64decode(raw)
        if len(_key) != 32:
            raise RuntimeError("MESSAGE_ENCRYPTION_KEY must be 32 bytes (base64-encoded).")
    return _key


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns base64(nonce + ciphertext)."""
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit nonce, unique per message
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a string produced by encrypt(). Returns plaintext."""
    key = _get_key()
    aesgcm = AESGCM(key)
    raw = base64.b64decode(ciphertext, validate=True)
    if len(raw) < 13:
        raise ValueError("Encrypted payload is too short.")
    nonce, ct = raw[:12], raw[12:]
    return aesgcm.decrypt(nonce, ct, None).decode()


def encrypt_maybe(text: str | None) -> str | None:
    """Encrypt if not None."""
    return encrypt(text) if text else None


def decrypt_maybe(text: str | None) -> str | None:
    """Best-effort decrypt for mixed plaintext/encrypted rows."""
    if text is None:
        return None

    # Legacy rows may contain plaintext (including emoji/non-ASCII text).
    # If it is not valid base64, keep it as-is.
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        return text

    # Our encrypted payload is nonce(12) + ciphertext/tag; smaller blobs are plaintext.
    if len(raw) < 13:
        return text

    try:
        key = _get_key()
        aesgcm = AESGCM(key)
        nonce, ct = raw[:12], raw[12:]
        return aesgcm.decrypt(nonce, ct, None).decode()
    except (InvalidTag, UnicodeDecodeError, ValueError):
        return text
