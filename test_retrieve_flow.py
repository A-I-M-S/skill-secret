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
    def __init__(self, env_file, password="password123", query="anything"):
        super().__init__()
        self.env_file = env_file
        self.password = password
        self.query = query


def _write_env_with_token(env_path, password, url="https://abc.supabase.co",
                          token_plaintext="FAKE_TOKEN"):
    blob = crypto.encrypt_token(token_plaintext, password)
    b64 = base64.b64encode(blob).decode("ascii")
    envfile.write(env_path, {
        flows.ENV_KEY_BACKEND: "supabase",
        flows.ENV_KEY_PROJECT_URL: url,
        flows.ENV_KEY_API_BLOB: b64,
    })


def _run_handle_retrieve(args):
    from io import StringIO
    out_buf = StringIO()
    err_buf = StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_buf, err_buf
    code = 0
    try:
        flows.handle_retrieve(args)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return out_buf.getvalue(), err_buf.getvalue(), code


class TestRetrieveFlow(unittest.TestCase):
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

    def test_01_top1_match_success(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeSupabaseClient.RPC.execute.return_value = SimpleNamespace(
            data=[{"id": "r1", "title": "title-1", "body": "secret content",
                   "rank": 0.9}]
        )

        out, err, code = _run_handle_retrieve(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            query="secret",
        ))

        self.assertEqual(code, 0, f"unexpected exit; out={out!r} err={err!r}")
        self.assertIn("--- MATCH FOUND ---", out)
        self.assertIn("secret content", out)
        self.assertIn("MODE: supabase", err)

    def test_02_no_match_exits_0_with_mode_banner(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeSupabaseClient.RPC.execute.return_value = SimpleNamespace(data=[])

        out, err, code = _run_handle_retrieve(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            query="nothing",
        ))

        self.assertEqual(code, 0)
        self.assertIn(
            "No highly relevant information found matching those parameters.",
            out,
        )
        self.assertIn("MODE: supabase", err,
                      "MODE banner must still appear on no-match")

    def test_03_wrong_password_exits_2(self):
        _write_env_with_token(self._env_path, self.PASSWORD)

        out, err, code = _run_handle_retrieve(_Args(
            env_file=self._env_path,
            password="wrong",
            query="anything",
        ))

        self.assertEqual(code, 2)
        self.assertIn("Wrong password", out)
        self.assertNotIn("MODE: supabase", err,
                         "MODE banner must NOT appear on wrong password")
        _FakeSupabaseClient.RPC.execute.assert_not_called()

    def test_04_not_initialized_exits_4(self):
        out, err, code = _run_handle_retrieve(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            query="anything",
        ))

        self.assertEqual(code, 4)
        self.assertIn("Not initialized. Run init first.", out)
        self.assertNotIn("MODE: supabase", err,
                         "MODE banner must NOT appear when not initialized")
        _FakeSupabaseClient.RPC.execute.assert_not_called()

    def test_05_supabase_api_error_exits_3(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeSupabaseClient.RPC.execute.side_effect = Exception("api exploded")

        out, err, code = _run_handle_retrieve(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            query="anything",
        ))

        self.assertEqual(code, 3)
        self.assertIn("Supabase API error", out)
        self.assertIn("api exploded", out)

    def test_06_server_filtered_bootstrap_rows(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeSupabaseClient.RPC.execute.return_value = SimpleNamespace(
            data=[
                {"id": "p-real", "title": "real note",
                 "body": "real body", "rank": 0.7},
            ]
        )

        out, err, code = _run_handle_retrieve(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            query="anything",
        ))

        self.assertEqual(code, 0)
        self.assertNotIn("THIS_IS_BOOTSTRAP_BODY_DO_NOT_LEAK", out,
                         "bootstrap body must not be surfaced")
        self.assertIn("real body", out)

    def test_07_missing_required_cli_flag_exits_2(self):
        result = subprocess.run(
            [sys.executable, os.path.join(HERE, "secret.py"), "retrieve"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        err = result.stderr
        self.assertTrue(
            ("usage:" in err.lower()) or ("--password" in err) or
            ("--query" in err) or
            ("the following arguments are required" in err.lower()),
            f"stderr missing argparse usage text: {err!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)