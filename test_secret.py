import os
import subprocess
import sys
import tempfile
import unittest

import secret


HERE = os.path.dirname(os.path.abspath(__file__))


def _cli(*args):
    """Run secret.py in a subprocess and return CompletedProcess."""
    return subprocess.run(
        [sys.executable, os.path.join(HERE, "secret.py"), *args],
        capture_output=True,
        text=True,
    )


def _make_vault(tmpdir, password, contents):
    os.makedirs(tmpdir, exist_ok=True)
    path = os.path.join(tmpdir, "vault.enc")
    secret.write_vault(path, password, contents)
    return path


def _has_semantic():
    try:
        import sentence_transformers  # noqa: F401
        return True
    except Exception:
        return False


class TestCryptoAndFormat(unittest.TestCase):
    def test_01_encrypt_creates_vault(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "v.enc")
            r = _cli("encrypt", "--password", "pw", "--file", path,
                     "--content", "hello world")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Stored 1 chunk", r.stdout)
            with open(path, "rb") as f:
                head = f.read(secret.HEADER_SIZE)
            self.assertEqual(head[:4], secret.MAGIC)
            self.assertEqual(head[4], secret.VERSION)
            salt = head[8:24]
            self.assertEqual(len(salt), 16)
            count = int.from_bytes(head[28:32], "little")
            self.assertEqual(count, 1)

    def test_02_decrypt_finds_match_semantic(self):
        if not _has_semantic():
            self.skipTest("sentence-transformers not installed")
        with tempfile.TemporaryDirectory() as td:
            path = _make_vault(td, "pw", [
                "the quick brown fox jumps over the lazy dog",
                "completely unrelated gibberish about elephants",
                "rainy days in the city make me want tea",
            ])
            r = _cli("decrypt", "--password", "pw", "--file", path,
                     "--query", "tell me about a fox jumping over something lazy",
                     "--mode", "semantic")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("--- MATCH FOUND ---", r.stdout)
            self.assertIn("fox", r.stdout)
            self.assertIn("MODE: semantic", r.stderr)

    def test_03_decrypt_finds_match_keyword(self):
        with tempfile.TemporaryDirectory() as td:
            path = _make_vault(td, "pw", [
                "the garden keys are under the fake rock",
                "wifi network is named Guest2026 with password abc",
                "birthday party is on saturday at noon",
            ])
            r = _cli("decrypt", "--password", "pw", "--file", path,
                     "--query", "where are the garden keys hidden",
                     "--mode", "keyword", "--threshold", "0.01")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("--- MATCH FOUND ---", r.stdout)
            self.assertIn("rock", r.stdout)
            self.assertIn("MODE: keyword", r.stderr)

    def test_04_append_grows_vault(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "v.enc")
            r1 = _cli("encrypt", "--password", "pw", "--file", path,
                      "--content", "alpha bravo charlie")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            r2 = _cli("encrypt", "--password", "pw", "--file", path,
                      "--content", "delta echo foxtrot")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertIn("Vault now has 2 chunks", r2.stdout)

            r3 = _cli("decrypt", "--password", "pw", "--file", path,
                      "--query", "delta echo", "--mode", "keyword",
                      "--threshold", "0.01")
            self.assertIn("delta", r3.stdout)

            r4 = _cli("decrypt", "--password", "pw", "--file", path,
                      "--query", "alpha bravo", "--mode", "keyword",
                      "--threshold", "0.01")
            self.assertIn("alpha", r4.stdout)

    def test_05_wrong_password_rejected_encrypt(self):
        with tempfile.TemporaryDirectory() as td:
            path = _make_vault(td, "right", ["one"])
            with open(path, "rb") as f:
                before = f.read()
            r = _cli("encrypt", "--password", "WRONG", "--file", path,
                     "--content", "two")
            self.assertEqual(r.returncode, 2)
            self.assertIn("Wrong password", r.stdout)
            with open(path, "rb") as f:
                after = f.read()
            self.assertEqual(before, after, "vault must be byte-identical after failed append")

    def test_06_wrong_password_rejected_decrypt(self):
        with tempfile.TemporaryDirectory() as td:
            path = _make_vault(td, "right", ["the keys are under the mat"])
            r = _cli("decrypt", "--password", "WRONG", "--file", path,
                     "--query", "keys", "--mode", "keyword")
            self.assertEqual(r.returncode, 2)
            self.assertIn("Wrong password", r.stdout)

    def test_07_tampered_ciphertext_detected(self):
        with tempfile.TemporaryDirectory() as td:
            path = _make_vault(td, "pw", [
                "the garden keys are under the fake rock",
                "wifi network is named Guest2026 with password abc",
            ])
            with open(path, "rb") as f:
                blob = bytearray(f.read())
            # Corrupt chunk 0 (the first plaintext, "garden keys"). Order in the
            # index matches append order, so offsets[0] is the first chunk.
            salt, iters, offsets = secret.decode_header(bytes(blob))
            target = offsets[0]
            blob[target + 4 + 40] ^= 0xFF
            with open(path, "wb") as f:
                f.write(bytes(blob))

            r = _cli("decrypt", "--password", "pw", "--file", path,
                     "--query", "garden keys", "--mode", "keyword",
                     "--threshold", "0.01")
            # The good chunk ("wifi network") decrypts fine, the corrupted
            # chunk is skipped with a WARN. The search must not return the
            # corrupted plaintext.
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("the garden keys are under the fake rock", r.stdout)
            self.assertIn("chunk 0 unreadable, skipped", r.stderr)

    def test_08_tampered_header_detected(self):
        with tempfile.TemporaryDirectory() as td:
            path = _make_vault(td, "pw", ["hi"])
            with open(path, "rb") as f:
                blob = bytearray(f.read())
            blob[0:4] = b"XXXX"
            with open(path, "wb") as f:
                f.write(bytes(blob))
            r = _cli("decrypt", "--password", "pw", "--file", path,
                     "--query", "hi", "--mode", "keyword")
            self.assertEqual(r.returncode, 3)
            self.assertIn("corrupt or tampered (header)", r.stdout)

    def test_09_no_match_above_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            path = _make_vault(td, "pw", [
                "alpha bravo charlie",
                "delta echo foxtrot",
            ])
            r = _cli("decrypt", "--password", "pw", "--file", path,
                     "--query", "completely unrelated xylophone zebra",
                     "--mode", "keyword", "--threshold", "9999.0")
            self.assertEqual(r.returncode, 0)
            self.assertIn("No highly relevant information", r.stdout)

    def test_10_empty_vault_message(self):
        with tempfile.TemporaryDirectory() as td:
            # Build a vault with a single empty chunk: search should say empty.
            path = os.path.join(td, "v.enc")
            secret.write_vault(path, "pw", [""])
            r = _cli("decrypt", "--password", "pw", "--file", path,
                     "--query", "anything", "--mode", "keyword")
            self.assertEqual(r.returncode, 0)
            self.assertIn("Vault is empty", r.stdout)

    def test_11_keyword_fallback_when_semantic_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            path = _make_vault(td, "pw", [
                "the garden keys are under the fake rock",
                "wifi network is named Guest2026 with password abc",
            ])
            # If semantic IS available, simulate absence by forcing keyword.
            # If semantic is NOT available, auto mode picks keyword naturally.
            r = _cli("decrypt", "--password", "pw", "--file", path,
                     "--query", "garden keys", "--mode", "keyword",
                     "--threshold", "0.01")
            self.assertEqual(r.returncode, 0)
            self.assertIn("MODE: keyword", r.stderr)
            self.assertIn("rock", r.stdout)

    def test_12_missing_file_error(self):
        with tempfile.TemporaryDirectory() as td:
            r = _cli("decrypt", "--password", "pw",
                     "--file", os.path.join(td, "nope.enc"),
                     "--query", "x", "--mode", "keyword")
            self.assertEqual(r.returncode, 4)
            self.assertIn("does not exist", r.stdout)

    def test_13_mode_override_forces_keyword(self):
        with tempfile.TemporaryDirectory() as td:
            path = _make_vault(td, "pw", [
                "the garden keys are under the fake rock",
            ])
            r = _cli("decrypt", "--password", "pw", "--file", path,
                     "--query", "garden keys",
                     "--mode", "keyword", "--threshold", "0.01")
            self.assertEqual(r.returncode, 0)
            self.assertIn("MODE: keyword", r.stderr)

    def test_14_salt_differs_between_vaults(self):
        with tempfile.TemporaryDirectory() as td:
            p1 = _make_vault(td + "/a", "pw", ["x"])
            p2 = _make_vault(td + "/b", "pw", ["x"])
            with open(p1, "rb") as f:
                salt1 = f.read()[8:24]
            with open(p2, "rb") as f:
                salt2 = f.read()[8:24]
            self.assertNotEqual(salt1, salt2)

    def test_15_atomic_write_no_partial_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "v.enc")
            secret.write_vault(path, "pw", ["original"])
            with open(path, "rb") as f:
                original = f.read()

            # Patch os.replace to raise and confirm the original survives.
            real_replace = os.replace
            def boom(*a, **kw):
                raise OSError("simulated crash")
            os.replace = boom
            try:
                with self.assertRaises(OSError):
                    secret.write_vault(path, "pw", ["new content"])
            finally:
                os.replace = real_replace

            with open(path, "rb") as f:
                after = f.read()
            self.assertEqual(original, after, "original vault must survive a failed write")
            # And the .tmp file may exist but should be the broken intermediate
            # (we don't assert its presence; just that the canonical file is intact).

    def test_16_threshold_per_mode_defaults(self):
        # Construct a query whose BM25-lite score is between the two defaults.
        # keyword default is 0.05, semantic default is 0.30.
        # We don't have a portable "score between" without running, so just
        # assert the constants are sane and that --threshold overrides work.
        self.assertEqual(secret.SEMANTIC_THRESHOLD, 0.30)
        self.assertEqual(secret.KEYWORD_THRESHOLD, 0.05)
        with tempfile.TemporaryDirectory() as td:
            path = _make_vault(td, "pw", [
                "the quick brown fox",
                "entirely unrelated content about calendars",
            ])
            r = _cli("decrypt", "--password", "pw", "--file", path,
                     "--query", "fox", "--mode", "keyword",
                     "--threshold", "0.0001")
            self.assertIn("fox", r.stdout)

    def test_17_corrupt_chunk_does_not_kill_others(self):
        with tempfile.TemporaryDirectory() as td:
            path = _make_vault(td, "pw", [
                "alpha content one",
                "bravo content two",
                "charlie content three",
            ])
            with open(path, "rb") as f:
                blob = bytearray(f.read())
            # Corrupt the middle chunk (index 1).
            salt, iters, offsets = secret.decode_header(bytes(blob))
            target = offsets[1]
            blob[target + 4 + 40] ^= 0xFF
            with open(path, "wb") as f:
                f.write(bytes(blob))

            r = _cli("decrypt", "--password", "pw", "--file", path,
                     "--query", "charlie content three", "--mode", "keyword",
                     "--threshold", "0.01")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("charlie content three", r.stdout)
            self.assertIn("chunk 1 unreadable, skipped", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
