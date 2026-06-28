from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
import types
import unittest
from argparse import Namespace
from types import SimpleNamespace
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
    def __init__(self, env_file, password="password123", content="hello world"):
        super().__init__()
        self.env_file = env_file
        self.password = password
        self.content = content


def _write_env_with_token(env_path, password, url="https://abc.supabase.co",
                          token_plaintext="FAKE_TOKEN"):
    blob = crypto.encrypt_token(token_plaintext, password)
    b64 = base64.b64encode(blob).decode("ascii")
    envfile.write(env_path, {
        flows.ENV_KEY_BACKEND: "supabase",
        flows.ENV_KEY_PROJECT_URL: url,
        flows.ENV_KEY_API_BLOB: b64,
    })


def _run_handle_take(args):
    from io import StringIO
    buf = StringIO()
    real = sys.stdout
    sys.stdout = buf
    code = 0
    try:
        flows.handle_take(args)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.stdout = real
    return buf.getvalue(), code


class TestTakeFlow(unittest.TestCase):
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

    def test_01_happy_path_creates_row_in_notes(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeSupabaseClient.TABLE.insert.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "row-new"}])
        )

        out, code = _run_handle_take(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            content="hello world",
        ))

        self.assertEqual(code, 0, f"unexpected exit; stdout={out!r}")
        self.assertIn("SUCCESS: Stored. Row id ", out)
        self.assertIn("row-new", out)

        _FakeSupabaseClient.TABLE.insert.assert_called_once()
        row = _FakeSupabaseClient.TABLE.insert.call_args.args[0]
        self.assertEqual(row["body"], "hello world")
        self.assertEqual(row["kind"], "note")

    def test_02_wrong_password_exits_2(self):
        _write_env_with_token(self._env_path, self.PASSWORD)

        out, code = _run_handle_take(_Args(
            env_file=self._env_path,
            password="wrong",
            content="hello world",
        ))

        self.assertEqual(code, 2)
        self.assertIn("Wrong password", out)
        _FakeSupabaseClient.TABLE.insert.assert_not_called()

    def test_03_not_initialized_exits_4(self):
        out, code = _run_handle_take(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            content="hello world",
        ))

        self.assertEqual(code, 4)
        self.assertIn("Not initialized. Run init first.", out)
        _FakeSupabaseClient.TABLE.insert.assert_not_called()

    def test_04_supabase_api_error_exits_3(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeSupabaseClient.TABLE.insert.return_value.execute.side_effect = (
            Exception("api down")
        )

        out, code = _run_handle_take(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            content="hello world",
        ))

        self.assertEqual(code, 3)
        self.assertIn("Supabase API error", out)
        self.assertIn("api down", out)

    def test_05_missing_required_cli_flag_exits_2(self):
        result = subprocess.run(
            [sys.executable, os.path.join(HERE, "secret.py"), "take"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        err = result.stderr
        self.assertTrue(
            ("usage:" in err.lower()) or ("--password" in err) or
            ("--content" in err) or
            ("the following arguments are required" in err.lower()),
            f"stderr missing argparse usage text: {err!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)