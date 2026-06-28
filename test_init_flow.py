from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    def __init__(self, env_file, notion_token="tok", parent_page_id="parent-1",
                 password="password123"):
        super().__init__()
        self.env_file = env_file
        self.notion_token = notion_token
        self.parent_page_id = parent_page_id
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

    def test_01_happy_path_writes_env_and_calls_notion(self):
        _FakeNotionClient.DATABASES.create.return_value = {"id": "db-new"}

        out, code = _run_handle_init(_Args(env_file=self._env_path))

        self.assertEqual(code, 0, f"unexpected exit; stdout={out!r}")
        self.assertTrue(
            out.startswith("SUCCESS: KMS initialized. Database skill-secret-vault ("),
            f"stdout did not start with success prefix: {out!r}",
        )

        self.assertTrue(
            os.path.exists(self._env_path),
            ".env must be written on happy path",
        )

        data = envfile.read(self._env_path)
        self.assertIn(flows.ENV_KEY_DB_ID, data)
        self.assertIn(flows.ENV_KEY_PARENT_PAGE_ID, data)
        self.assertIn(flows.ENV_KEY_API_BLOB, data)
        self.assertEqual(data[flows.ENV_KEY_PARENT_PAGE_ID], "parent-1")

        _FakeNotionClient.USERS.me.assert_called_once()
        _FakeNotionClient.DATABASES.create.assert_called_once()
        _FakeNotionClient.PAGES.create.assert_called_once()

        create_kwargs = _FakeNotionClient.PAGES.create.call_args.kwargs
        body = create_kwargs["properties"]["Body"]["rich_text"][0]["text"]["content"]
        self.assertEqual(body, data[flows.ENV_KEY_API_BLOB])

    def test_02_already_initialized_exits_5(self):
        envfile.write(self._env_path, {
            flows.ENV_KEY_DB_ID: "db-existing",
            flows.ENV_KEY_PARENT_PAGE_ID: "p",
            flows.ENV_KEY_API_BLOB: "x",
        })

        out, code = _run_handle_init(_Args(env_file=self._env_path))

        self.assertEqual(code, 5)
        self.assertIn("Already initialized", out)
        _FakeNotionClient.USERS.me.assert_not_called()
        _FakeNotionClient.DATABASES.create.assert_not_called()
        _FakeNotionClient.PAGES.create.assert_not_called()

    def test_03_wrong_token_exits_3(self):
        _FakeNotionClient.USERS.me.side_effect = Exception("invalid token")

        out, code = _run_handle_init(_Args(env_file=self._env_path))

        self.assertEqual(code, 3)
        self.assertIn("Notion API rejected the token", out)
        self.assertIn("invalid token", out)
        self.assertFalse(
            os.path.exists(self._env_path),
            ".env must NOT be written when token is rejected",
        )

    def test_04_parent_page_not_accessible_exits_3(self):
        _FakeNotionClient.SEARCH.side_effect = Exception("page inaccessible")

        out, code = _run_handle_init(_Args(env_file=self._env_path))

        self.assertEqual(code, 3)
        self.assertIn("Could not create database", out)
        self.assertIn("page inaccessible", out)
        self.assertFalse(
            os.path.exists(self._env_path),
            ".env must NOT be written when DB create fails",
        )

    def test_05_env_write_fails_exits_3(self):
        _FakeNotionClient.DATABASES.create.return_value = {"id": "db-new"}

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
            ("usage:" in err.lower()) or ("--notion-token" in err) or
            ("the following arguments are required" in err.lower()),
            f"stderr missing argparse usage text: {err!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)