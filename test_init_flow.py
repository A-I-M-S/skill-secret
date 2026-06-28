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
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    def __init__(self, env_file, url="https://abc.supabase.co",
                 api_key="eyJhbGciOiJIUzI1NiJ9.fake.jwt",
                 password="password123"):
        super().__init__()
        self.env_file = env_file
        self.url = url
        self.api_key = api_key
        self.password = password


def _run_handle_init(args):
    """Run handle_init and capture (stdout, exit_code)."""
    from io import StringIO
    buf = StringIO()
    real = sys.stdout
    sys.stdout = buf
    code = 0
    try:
        flows.handle_init(args)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.stdout = real
    return buf.getvalue(), code


class TestInitFlow(unittest.TestCase):
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

    def test_01_happy_path_writes_env_and_calls_supabase(self):
        _FakeSupabaseClient.AUTH.get_user.return_value = {
            "user": None,
            "region": "us-west-1",
        }
        _FakeSupabaseClient.TABLE.select.return_value.limit.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "schema-ok"}])
        )
        _FakeSupabaseClient.TABLE.delete.return_value.eq.return_value.execute.return_value = (
            SimpleNamespace(data=[])
        )
        _FakeSupabaseClient.TABLE.insert.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "boot-row-id"}])
        )

        out, code = _run_handle_init(_Args(env_file=self._env_path))

        self.assertEqual(code, 0, f"unexpected exit; stdout={out!r}")
        self.assertTrue(
            out.startswith(
                "SUCCESS: KMS initialized. Database abc (https://abc.supabase.co)."
            ),
            f"stdout did not start with success prefix: {out!r}",
        )

        self.assertTrue(
            os.path.exists(self._env_path),
            ".env must be written on happy path",
        )

        data = envfile.read(self._env_path)
        self.assertEqual(data[flows.ENV_KEY_BACKEND], "supabase")
        self.assertEqual(data[flows.ENV_KEY_PROJECT_URL], "https://abc.supabase.co")
        self.assertIn(flows.ENV_KEY_API_BLOB, data)

        _FakeSupabaseClient.AUTH.get_user.assert_called_once()
        _FakeSupabaseClient.TABLE.select.assert_called_once_with("id")
        _FakeSupabaseClient.TABLE.insert.assert_called_once()
        row = _FakeSupabaseClient.TABLE.insert.call_args.args[0]
        self.assertEqual(row["kind"], "bootstrap")
        self.assertEqual(row["body"], data[flows.ENV_KEY_API_BLOB])

    def test_02_already_initialized_exits_5(self):
        envfile.write(self._env_path, {
            flows.ENV_KEY_BACKEND: "supabase",
            flows.ENV_KEY_PROJECT_URL: "https://abc.supabase.co",
            flows.ENV_KEY_API_BLOB: "x",
        })

        out, code = _run_handle_init(_Args(env_file=self._env_path))

        self.assertEqual(code, 5)
        self.assertIn("Already initialized", out)
        _FakeSupabaseClient.AUTH.get_user.assert_not_called()
        _FakeSupabaseClient.TABLE.insert.assert_not_called()

    def test_03_wrong_token_exits_3(self):
        _FakeSupabaseClient.AUTH.get_user.side_effect = Exception("invalid token")

        out, code = _run_handle_init(_Args(env_file=self._env_path))

        self.assertEqual(code, 3)
        self.assertIn("Supabase API rejected the api-key", out)
        self.assertIn("invalid token", out)
        self.assertFalse(
            os.path.exists(self._env_path),
            ".env must NOT be written when token is rejected",
        )

    def test_04_schema_missing_exits_3(self):
        _FakeSupabaseClient.AUTH.get_user.return_value = {"user": None}
        _FakeSupabaseClient.TABLE.select.return_value.limit.return_value.execute.side_effect = (
            Exception("schema not provisioned. Run the SQL in _SCHEMA_SQL once")
        )

        out, code = _run_handle_init(_Args(env_file=self._env_path))

        self.assertEqual(code, 3)
        self.assertIn("Could not verify schema", out)
        self.assertIn("schema not provisioned", out)
        self.assertFalse(
            os.path.exists(self._env_path),
            ".env must NOT be written when schema check fails",
        )

    def test_05_env_write_fails_exits_3(self):
        _FakeSupabaseClient.AUTH.get_user.return_value = {"user": None}
        _FakeSupabaseClient.TABLE.select.return_value.limit.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "schema-ok"}])
        )
        _FakeSupabaseClient.TABLE.delete.return_value.eq.return_value.execute.return_value = (
            SimpleNamespace(data=[])
        )
        _FakeSupabaseClient.TABLE.insert.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "boot-row-id"}])
        )

        def boom(path, data):
            raise OSError("disk full")

        with patch("flows.envfile.write", side_effect=boom):
            out, code = _run_handle_init(_Args(env_file=self._env_path))

        self.assertEqual(code, 3)
        self.assertIn("Could not write .env", out)
        self.assertIn("disk full", out)
        self.assertFalse(
            os.path.exists(self._env_path),
            ".env must NOT exist after envfile.write fails",
        )

    def test_06_missing_required_cli_flag_exits_2(self):
        result = subprocess.run(
            [sys.executable, os.path.join(HERE, "secret.py"), "init"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        err = result.stderr
        self.assertTrue(
            ("usage:" in err.lower()) or ("--url" in err) or
            ("--api-key" in err) or
            ("the following arguments are required" in err.lower()),
            f"stderr missing argparse usage text: {err!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)