from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
import types
import unittest
from argparse import Namespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crypto
import envfile
import flows
import supabase_kms


HERE = os.path.dirname(os.path.abspath(__file__))


class _FakeSupabaseClient:
    AUTH = MagicMock()
    TABLE = MagicMock()
    RPC = MagicMock()

    def __init__(self, url=None, key=None):
        self._url = url
        self._key = key
        self.auth = _FakeSupabaseClient.AUTH
        self.table = MagicMock(return_value=_FakeSupabaseClient.TABLE)
        self.rpc = MagicMock(return_value=_FakeSupabaseClient.RPC)

    @classmethod
    def reset_mocks(cls):
        cls.AUTH = MagicMock()
        cls.TABLE = MagicMock()
        cls.RPC = MagicMock()


def _install_fake_supabase():
    _FakeSupabaseClient.reset_mocks()
    mod = types.ModuleType("supabase")
    mod.create_client = MagicMock(side_effect=_FakeSupabaseClient)
    mod.Client = MagicMock()
    saved = sys.modules.get("supabase")
    sys.modules["supabase"] = mod
    return mod, saved


def _restore_supabase(saved):
    if saved is None:
        sys.modules.pop("supabase", None)
    else:
        sys.modules["supabase"] = saved


class _Args(Namespace):
    def __init__(self, env_file, password="password123"):
        super().__init__()
        self.env_file = env_file
        self.password = password


def _write_env_with_token(env_path, password, url="https://abc.supabase.co",
                          token_plaintext="FAKE_TOKEN"):
    blob = crypto.encrypt_token(token_plaintext, password)
    b64 = base64.b64encode(blob).decode("ascii")
    envfile.write(env_path, {
        flows.ENV_KEY_BACKEND: "supabase",
        flows.ENV_KEY_PROJECT_URL: url,
        flows.ENV_KEY_API_BLOB: b64,
    })


def _run_handle_whoami(args):
    from io import StringIO
    out_buf = StringIO()
    err_buf = StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_buf, err_buf
    code = 0
    try:
        flows.handle_whoami(args)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return out_buf.getvalue(), err_buf.getvalue(), code


class TestWhoamiFlow(unittest.TestCase):
    PASSWORD = "password123"

    def setUp(self):
        self._mod, self._saved = _install_fake_supabase()
        self._tmp = tempfile.TemporaryDirectory(dir=os.getcwd())
        self.addCleanup(self._tmp.cleanup)
        self._env_path = os.path.join(self._tmp.name, ".env")

    def tearDown(self):
        _restore_supabase(self._saved)
        for stale in (self._env_path, self._env_path + ".tmp"):
            if os.path.exists(stale):
                try:
                    os.unlink(stale)
                except OSError:
                    pass

    def test_01_happy_path_shows_account(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeSupabaseClient.AUTH.get_user.return_value = {
            "user": None,
            "region": "us-west-1",
        }

        out, err, code = _run_handle_whoami(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
        ))

        self.assertEqual(code, 0, f"unexpected exit; out={out!r} err={err!r}")
        self.assertTrue(
            out.startswith("--- ACCOUNT ---"),
            f"stdout did not start with --- ACCOUNT ---: {out!r}",
        )
        self.assertIn("project: https://abc.supabase.co", out)
        self.assertIn("anon_key: FAKE_TOK", out)
        _FakeSupabaseClient.AUTH.get_user.assert_called_once()

    def test_02_wrong_password_exits_2(self):
        _write_env_with_token(self._env_path, self.PASSWORD)

        out, err, code = _run_handle_whoami(_Args(
            env_file=self._env_path,
            password="wrong",
        ))

        self.assertEqual(code, 2)
        self.assertIn("Wrong password", out)
        _FakeSupabaseClient.AUTH.get_user.assert_not_called()

    def test_03_not_initialized_exits_4(self):
        out, err, code = _run_handle_whoami(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
        ))

        self.assertEqual(code, 4)
        self.assertIn("Not initialized. Run init first.", out)
        _FakeSupabaseClient.AUTH.get_user.assert_not_called()

    def test_04_supabase_api_error_exits_3(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeSupabaseClient.AUTH.get_user.side_effect = Exception("whoami down")

        out, err, code = _run_handle_whoami(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
        ))

        self.assertEqual(code, 3)
        self.assertIn("Supabase API error", out)
        self.assertIn("whoami down", out)

    def test_05_token_never_echoed_anonymous_path(self):
        sentinel = "tok_DEADBEEF_cafebabe_SECRET_NEVER_LEAK"
        _write_env_with_token(
            self._env_path, self.PASSWORD,
            token_plaintext=sentinel,
        )
        _FakeSupabaseClient.AUTH.get_user.return_value = {
            "user": None,
            "region": "us-west-1",
        }

        out, err, code = _run_handle_whoami(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
        ))

        self.assertEqual(code, 0)
        self.assertNotIn(sentinel, out,
                         "plaintext token leaked into stdout")
        self.assertNotIn(sentinel, err,
                         "plaintext token leaked into stderr")

        with open(self._env_path, "rb") as fh:
            raw = fh.read()
        self.assertNotIn(sentinel.encode("utf-8"), raw,
                         ".env should hold the encrypted blob, not the plaintext")

    def test_06_missing_required_cli_flag_exits_2(self):
        result = subprocess.run(
            [sys.executable, os.path.join(HERE, "secret.py"), "whoami"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        err = result.stderr
        self.assertTrue(
            ("usage:" in err.lower()) or ("--password" in err) or
            ("the following arguments are required" in err.lower()),
            f"stderr missing argparse usage text: {err!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)