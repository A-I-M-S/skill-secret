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
    def __init__(self, env_file, password="password123", content="hello world"):
        super().__init__()
        self.env_file = env_file
        self.password = password
        self.content = content


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

    def test_01_happy_path_creates_page_in_db(self):
        _FakeNotionClient.PAGES.create.return_value = {"id": "page-new"}
        _write_env_with_token(self._env_path, self.PASSWORD)

        out, code = _run_handle_take(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            content="hello world",
        ))

        self.assertEqual(code, 0, f"unexpected exit; stdout={out!r}")
        self.assertIn("SUCCESS: Stored. Page id ", out)
        self.assertIn("page-new", out)

        _FakeNotionClient.PAGES.create.assert_called_once()
        kwargs = _FakeNotionClient.PAGES.create.call_args.kwargs
        self.assertEqual(kwargs["parent"], {"type": "database_id", "database_id": "db-1"})
        body = kwargs["properties"]["Body"]["rich_text"][0]["text"]["content"]
        self.assertEqual(body, "hello world")
        self.assertEqual(kwargs["properties"]["Kind"]["select"]["name"], "note")

    def test_02_wrong_password_exits_2(self):
        _write_env_with_token(self._env_path, self.PASSWORD)

        out, code = _run_handle_take(_Args(
            env_file=self._env_path,
            password="wrong",
            content="hello world",
        ))

        self.assertEqual(code, 2)
        self.assertIn("Wrong password", out)
        _FakeNotionClient.PAGES.create.assert_not_called()

    def test_03_not_initialized_exits_4(self):
        out, code = _run_handle_take(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            content="hello world",
        ))

        self.assertEqual(code, 4)
        self.assertIn("Not initialized. Run init first.", out)
        _FakeNotionClient.PAGES.create.assert_not_called()

    def test_04_notion_api_error_exits_3(self):
        _write_env_with_token(self._env_path, self.PASSWORD)
        _FakeNotionClient.PAGES.create.side_effect = Exception("api down")

        out, code = _run_handle_take(_Args(
            env_file=self._env_path,
            password=self.PASSWORD,
            content="hello world",
        ))

        self.assertEqual(code, 3)
        self.assertIn("Notion API error", out)
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