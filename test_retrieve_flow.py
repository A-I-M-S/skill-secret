from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crypto
import envfile
import flows
import notion_kms


HERE = os.path.dirname(os.path.abspath(__file__))


class _FakeNotionClient:
    USERS = MagicMock()
    DATABASES = MagicMock()
    PAGES = MagicMock()
    SEARCH = MagicMock()

    def __init__(self, auth=None):
        self.auth = auth
        self.users = _FakeNotionClient.USERS
        self.databases = _FakeNotionClient.DATABASES
        self.pages = _FakeNotionClient.PAGES
        self.search = _FakeNotionClient.SEARCH

    @classmethod
    def reset_mocks(cls):
        cls.USERS = MagicMock()
        cls.DATABASES = MagicMock()
        cls.PAGES = MagicMock()
        cls.SEARCH = MagicMock()


def _install_fake_notion():
    import types

    _FakeNotionClient.reset_mocks()
    mod = types.ModuleType("notion_client")
    mod.Client = MagicMock(side_effect=_FakeNotionClient)
    saved = sys.modules.get("notion_client")
    sys.modules["notion_client"] = mod
    return mod, saved


def _restore_notion(saved):
    if saved is None:
        sys.modules.pop("notion_client", None)
    else:
        sys.modules["notion_client"] = saved


class _Args(Namespace):
    def __init__(self, env_file, password="password123", query="anything"):
        super().__init__()
        self.env_file = env_file
        self.password = password
        self.query = query


def _write_env_with_token(env_path, password, db_id="db-1",
                          parent_page_id="parent-1",
                          token_plaintext="FAKE_TOKEN"):
    blob = crypto.encrypt_token(token_plaintext, password)
    b64 = base64.b64encode(blob).decode("ascii")
    envfile.write(env_path, {
        flows.ENV_KEY_DB_ID: db_id,
        flows.ENV_KEY_PARENT_PAGE_ID: parent_page_id,
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


def _make_page(page_id, db_id, title, body, kind="note"):
    return {
        "id": page_id,
        "parent": {"type": "database_id", "database_id": db_id},
        "properties": {
            "Name": {"title": [{"plain_text": title}]},
            "Body": {"rich_text": [{"plain_text": body}]},
            "Kind": {"select": {"name": kind}},
        },
    }


class TestRetrieveFlow(unittest.TestCase):
    PASSWORD = "password123"

    def setUp(self):
        self._mod, self._saved = _install_fake_notion()
        self._tmp = tempfile.TemporaryDirectory(dir=os.getcwd())
        self.addCleanup(self._tmp.cleanup)
        self._env_path = os.path.join(self._tmp.name, ".env")

    def tearDown(self):
        _restore_notion(self._saved)
        for stale in (self._env_path, self._env_path + ".tmp"):
            if os.path.exists(stale):
                try:
                    os.unlink(stale)
                except OSError:
                    pass

    def test_01_top1_match_success(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeNotionClient.SEARCH.return_value = {
            "results": [
                _make_page("p1", "db-1", "title-1", "secret content"),
            ]
        }

        out, err, code = _run_handle_retrieve(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            query="secret",
        ))

        self.assertEqual(code, 0, f"unexpected exit; out={out!r} err={err!r}")
        self.assertIn("--- MATCH FOUND ---", out)
        self.assertIn("secret content", out)
        self.assertIn("MODE: notion", err)

    def test_02_no_match_exits_0_with_mode_banner(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeNotionClient.SEARCH.return_value = {"results": []}

        out, err, code = _run_handle_retrieve(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            query="nothing",
        ))

        self.assertEqual(code, 0)
        self.assertIn("No highly relevant information found matching those parameters.", out)
        self.assertIn("MODE: notion", err,
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
        self.assertNotIn("MODE: notion", err,
                         "MODE banner must NOT appear on wrong password")
        _FakeNotionClient.SEARCH.assert_not_called()

    def test_04_not_initialized_exits_4(self):
        out, err, code = _run_handle_retrieve(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            query="anything",
        ))

        self.assertEqual(code, 4)
        self.assertIn("Not initialized. Run init first.", out)
        self.assertNotIn("MODE: notion", err,
                         "MODE banner must NOT appear when not initialized")
        _FakeNotionClient.SEARCH.assert_not_called()

    def test_05_notion_api_error_exits_3(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeNotionClient.SEARCH.side_effect = Exception("api exploded")

        out, err, code = _run_handle_retrieve(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            query="anything",
        ))

        self.assertEqual(code, 3)
        self.assertIn("Notion API error", out)
        self.assertIn("api exploded", out)
        # Note: flows.handle_retrieve writes "MODE: notion" to stderr BEFORE the
        # search call, so it appears here too. We don't assert absence; we just
        # verify the error path.

    def test_06_bootstrap_excluded(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeNotionClient.SEARCH.return_value = {
            "results": [
                _make_page("p-boot", "db-1", "__bootstrap__",
                           "THIS_IS_BOOTSTRAP_BODY_DO_NOT_LEAK", kind="note"),
                _make_page("p-real", "db-1", "real note", "real body"),
            ]
        }

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