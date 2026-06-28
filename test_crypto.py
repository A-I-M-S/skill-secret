import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crypto
from cryptography.fernet import InvalidToken
from secret import DEFAULT_PBKDF2_ITERS, SALT_SIZE


class TestCrypto(unittest.TestCase):
    def test_01_round_trip_basic(self):
        blob = crypto.encrypt_token("hello", "pw")
        self.assertEqual(crypto.decrypt_token(blob, "pw"), "hello")

    def test_02_round_trip_unicode_and_empty(self):
        cases = [
            "",
            "ascii",
            "ascii with spaces and 1234",
            "unicode — 你好 — 🦊",
            "x" * 10_000,
        ]
        for pt in cases:
            blob = crypto.encrypt_token(pt, "pw")
            self.assertEqual(crypto.decrypt_token(blob, "pw"), pt)

    def test_03_salt_differs_per_call(self):
        b1 = crypto.encrypt_token("same", "same")
        b2 = crypto.encrypt_token("same", "same")
        self.assertNotEqual(b1[:SALT_SIZE], b2[:SALT_SIZE])
        self.assertNotEqual(b1, b2)

    def test_04_wrong_password_raises_invalid_token(self):
        blob = crypto.encrypt_token("secret", "right")
        with self.assertRaises(InvalidToken):
            crypto.decrypt_token(blob, "WRONG")

    def test_05_blob_self_contained(self):
        blob = crypto.encrypt_token("self-contained payload", "pw")
        decoded = crypto.decrypt_token(blob, "pw")
        self.assertEqual(decoded, "self-contained payload")
        salt = blob[:SALT_SIZE]
        (iters,) = struct.unpack("<I", blob[SALT_SIZE:SALT_SIZE + 4])
        self.assertEqual(iters, DEFAULT_PBKDF2_ITERS)
        self.assertEqual(len(salt), SALT_SIZE)

    def test_06_tamper_detection_in_fernet_token(self):
        blob = bytearray(crypto.encrypt_token("payload", "pw"))
        blob[SALT_SIZE + 4 + 8] ^= 0xFF
        with self.assertRaises(InvalidToken):
            crypto.decrypt_token(bytes(blob), "pw")

    def test_07_tamper_detection_in_salt(self):
        blob = bytearray(crypto.encrypt_token("payload", "pw"))
        blob[3] ^= 0xFF
        with self.assertRaises(InvalidToken):
            crypto.decrypt_token(bytes(blob), "pw")

    def test_08_truncated_blob_raises(self):
        with self.assertRaises(ValueError):
            crypto.decrypt_token(b"", "pw")
        with self.assertRaises(ValueError):
            crypto.decrypt_token(b"x" * (SALT_SIZE + 3), "pw")

    def test_09_iters_preserved_in_blob(self):
        blob = crypto.encrypt_token("p", "pw")
        (iters,) = struct.unpack("<I", blob[SALT_SIZE:SALT_SIZE + 4])
        self.assertEqual(iters, DEFAULT_PBKDF2_ITERS)


if __name__ == "__main__":
    unittest.main(verbosity=2)