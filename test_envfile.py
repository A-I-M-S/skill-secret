import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import base64

import crypto
import envfile


class TestEnvfile(unittest.TestCase):
    def test_01_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".env")
            envfile.write(path, {"A": "1"})
            self.assertEqual(envfile.read(path), {"A": "1"})

    def test_02_round_trip_multiple_keys(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".env")
            data = {
                "SKILL_SECRET_KMS_DB_ID": "00000000-0000-0000-0000-000000000001",
                "SKILL_SECRET_KMS_PARENT_PAGE_ID": "00000000-0000-0000-0000-000000000002",
                "SKILL_SECRET_KMS_API_BLOB": "aGVsbG8gd29ybGQ=",
            }
            envfile.write(path, data)
            self.assertEqual(envfile.read(path), data)

    def test_03_round_trip_empty_value(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".env")
            envfile.write(path, {"X": ""})
            self.assertEqual(envfile.read(path), {"X": ""})

    def test_04_read_missing_file_raises_filenotfound(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                envfile.read(os.path.join(td, "nope.env"))

    def test_05_read_malformed_line_raises_value_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".env")
            with open(path, "w") as f:
                f.write("NOEQUALS\n")
            with self.assertRaises(ValueError) as ctx:
                envfile.read(path)
            self.assertIn("malformed", str(ctx.exception))
            self.assertIn("line 1", str(ctx.exception))

    def test_06_read_duplicate_key_raises_value_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".env")
            with open(path, "w") as f:
                f.write("FOO=1\nFOO=2\n")
            with self.assertRaises(ValueError) as ctx:
                envfile.read(path)
            self.assertIn("FOO", str(ctx.exception))
            self.assertIn("duplicate", str(ctx.exception))

    def test_07_read_skips_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".env")
            with open(path, "w") as f:
                f.write("# header comment\n\n# another\nA=1\n\nB=2\n")
            self.assertEqual(envfile.read(path), {"A": "1", "B": "2"})

    def test_08_write_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "deep", "nested", ".env")
            envfile.write(path, {"X": "1"})
            self.assertTrue(os.path.exists(path))
            self.assertEqual(envfile.read(path), {"X": "1"})

    def test_09_write_mode_is_0o600(self):
        if os.name == "nt":
            self.skipTest("POSIX mode bits not enforced on Windows")
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".env")
            envfile.write(path, {"X": "1"})
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode, 0o600)

    def test_10_atomic_write_survives_crash(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".env")
            envfile.write(path, {"ORIGINAL": "yes"})
            with open(path, "rb") as f:
                before = f.read()

            real_replace = os.replace

            def boom(*a, **kw):
                raise OSError("simulated crash")

            os.replace = boom
            try:
                with self.assertRaises(OSError):
                    envfile.write(path, {"NEW": "broken"})
            finally:
                os.replace = real_replace

            with open(path, "rb") as f:
                after = f.read()
            self.assertEqual(before, after)

    def test_11_atomic_write_no_partial_final_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".env")
            envfile.write(path, {"ORIGINAL": "yes"})
            with open(path, "rb") as f:
                before = f.read()

            real_replace = os.replace

            def boom(*a, **kw):
                raise OSError("simulated crash")

            os.replace = boom
            try:
                envfile.write(path, {"NEW": "broken"})
            except OSError:
                pass
            finally:
                os.replace = real_replace

            self.assertEqual(envfile.read(path), {"ORIGINAL": "yes"})
            with open(path, "rb") as f:
                self.assertEqual(f.read(), before)

    def test_12_require_keys_returns_dict_when_all_present(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".env")
            envfile.write(path, {"A": "1", "B": "2", "C": "3"})
            out = envfile.require_keys(path, ["A", "C"])
            self.assertEqual(out, {"A": "1", "B": "2", "C": "3"})

    def test_13_require_keys_raises_naming_missing(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".env")
            envfile.write(path, {"A": "1"})
            with self.assertRaises(ValueError) as ctx:
                envfile.require_keys(
                    path,
                    ["SKILL_SECRET_KMS_DB_ID", "A"],
                )
            self.assertIn("SKILL_SECRET_KMS_DB_ID", str(ctx.exception))

    def test_14_write_does_not_persist_plaintext_token(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".env")
            plaintext_token = "secret_tok_abc123_super_secret"
            blob = crypto.encrypt_token(plaintext_token, "pw")
            b64 = base64.urlsafe_b64encode(blob).decode("ascii")
            envfile.write(
                path,
                {"SKILL_SECRET_KMS_API_BLOB": b64},
            )
            with open(path, "rb") as f:
                raw = f.read()
            self.assertNotIn(plaintext_token.encode("utf-8"), raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)