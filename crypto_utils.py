
import os
import base64
import json

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

SALT_LEN       = 16          # bytes
NONCE_LEN      = 12          # bytes  (NIST SP 800-38D §5.2.1.1)
PBKDF2_ITERS   = 310_000     # OWASP 2024 floor for SHA-256
KEY_LEN        = 32          # bytes → AES-256



def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LEN,
        salt=salt,
        iterations=PBKDF2_ITERS,
    )
    return kdf.derive(password.encode("utf-8"))



def encrypt(plaintext: str, password: str) -> str:
    salt  = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key   = _derive_key(password, salt)

    aesgcm      = AESGCM(key)
    ciphertext  = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

    raw = salt + nonce + ciphertext
    return base64.b64encode(raw).decode("ascii")


def decrypt(b64_blob: str, password: str) -> str:
    raw = base64.b64decode(b64_blob)

    if len(raw) < SALT_LEN + NONCE_LEN + 16:   # 16 = minimum GCM tag
        raise ValueError("Blob is too short — not a valid encrypted payload.")

    salt       = raw[:SALT_LEN]
    nonce      = raw[SALT_LEN : SALT_LEN + NONCE_LEN]
    ciphertext = raw[SALT_LEN + NONCE_LEN :]

    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)

    plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext_bytes.decode("utf-8")