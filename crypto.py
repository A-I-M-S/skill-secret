import base64
import hashlib
import hmac
import secrets
import struct

from cryptography.fernet import Fernet, InvalidToken

from secret import DEFAULT_PBKDF2_ITERS, SALT_SIZE, derive_key


BLOB_HEADER_SIZE = SALT_SIZE + 4

_ITER_FMT = "<I"
_ITER_LEN = struct.calcsize(_ITER_FMT)


def encrypt_token(plaintext: str, password: str) -> bytes:
    salt = secrets.token_bytes(SALT_SIZE)
    iters = DEFAULT_PBKDF2_ITERS
    key = derive_key(password, salt, iters)
    token = Fernet(key).encrypt(plaintext.encode("utf-8"))
    return salt + struct.pack(_ITER_FMT, iters) + token


def decrypt_token(blob: bytes, password: str) -> str:
    if len(blob) < BLOB_HEADER_SIZE:
        raise ValueError("token blob too short")
    salt = blob[:SALT_SIZE]
    (iters,) = struct.unpack(_ITER_FMT, blob[SALT_SIZE:BLOB_HEADER_SIZE])
    if iters < 1 or len(salt) != SALT_SIZE:
        raise ValueError("token blob header invalid")
    key = derive_key(password, salt, iters)
    plaintext = Fernet(key).decrypt(blob[BLOB_HEADER_SIZE:])
    return plaintext.decode("utf-8")


__all__ = ["encrypt_token", "decrypt_token", "InvalidToken"]