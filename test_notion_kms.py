import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import notion_kms


class _FakeNotionClient:
    def __init__(self, auth=None):
        self.auth = auth
        from unittest.mock import MagicMock
        self.users = MagicMock()
        self.databases = MagicMock()
        self.pages = MagicMock()
        self.search = MagicMock()


def _install_fake_notion():
    import sys
    import types
    from unittest.mock import MagicMock

    mod = types.ModuleType("notion_client")
    mod.Client = MagicMock(side_effect=_FakeNotionClient)
    saved = sys.modules.get("notion_client")
    sys.modules["notion_client"] = mod
    return mod, saved


def _restore_notion(saved):
    import sys
    if saved is None:
        sys.modules.pop("notion_client", None)
    else:
        sys.modules["notion_client"] = saved


class TestNotionKMS(unittest.TestCase):
    def setUp(self):
        self._mod, self._saved = _install_fake_notion()

    def tearDown(self):
        _restore_notion(self._saved)

    def test_01_whoami_returns_expected_keys(self):
        kms = notion_kms.NotionKMS("tok_xyz")
        kms._client.users.me.return_value = {
            "bot": {
                "id": "bot-123",
                "workspace_name": {"name": "My WS", "id": "ws-1"},
            }
        }
        info = kms.whoami()
        self.assertEqual(info["bot_id"], "bot-123")
        self.assertEqual(info["workspace_name"], "My WS")
        self.assertEqual(info["workspace_id"], "ws-1")
        for v in info.values():
            self.assertNotIn("tok_xyz", str(v))

    def test_02_whoami_api_error_wrapped(self):
        kms = notion_kms.NotionKMS("tok_xyz")
        kms._client.users.me.side_effect = Exception("boom")
        with self.assertRaises(notion_kms.NotionKMSError):
            kms.whoami()

    def test_03_ensure_database_creates_when_missing(self):
        kms = notion_kms.NotionKMS("tok")
        kms._client.search.return_value = {"results": []}
        kms._client.databases.create.return_value = {"id": "db-new"}
        db_id = kms.ensure_database("parent-1", "skill-secret-vault")
        self.assertEqual(db_id, "db-new")
        kms._client.databases.create.assert_called_once()
        kwargs = kms._client.databases.create.call_args.kwargs
        self.assertEqual(
            kwargs["parent"],
            {"type": "page_id", "page_id": "parent-1"},
        )
        self.assertIn("Name", kwargs["properties"])
        self.assertIn("Body", kwargs["properties"])
        self.assertIn("Kind", kwargs["properties"])

    def test_04_ensure_database_idempotent_returns_existing(self):
        kms = notion_kms.NotionKMS("tok")
        kms._client.search.return_value = {
            "results": [
                {
                    "id": "db-existing",
                    "title": [{"plain_text": "skill-secret-vault"}],
                }
            ]
        }
        kms._client.databases.retrieve.return_value = {
            "id": "db-existing",
            "parent": {"page_id": "parent-1"},
        }
        db_id = kms.ensure_database("parent-1", "skill-secret-vault")
        self.assertEqual(db_id, "db-existing")
        kms._client.databases.create.assert_not_called()

    def test_05_ensure_database_search_with_wrong_parent_creates_new(self):
        kms = notion_kms.NotionKMS("tok")
        kms._client.search.return_value = {
            "results": [
                {
                    "id": "db-other",
                    "title": [{"plain_text": "skill-secret-vault"}],
                }
            ]
        }
        kms._client.databases.retrieve.return_value = {
            "id": "db-other",
            "parent": {"page_id": "DIFFERENT"},
        }
        kms._client.databases.create.return_value = {"id": "db-created"}
        db_id = kms.ensure_database("parent-1", "skill-secret-vault")
        self.assertEqual(db_id, "db-created")
        kms._client.databases.create.assert_called_once()

    def test_06_create_page_truncates_name_at_80(self):
        kms = notion_kms.NotionKMS("tok")
        kms._client.pages.create.return_value = {"id": "p1"}
        long_content = "a" * 200
        kms.create_page("db1", long_content)
        props = kms._client.pages.create.call_args.kwargs["properties"]
        title_text = props["Name"]["title"][0]["text"]["content"]
        self.assertEqual(len(title_text), 80)
        self.assertEqual(title_text, "a" * 80)

    def test_07_create_page_empty_content_uses_uuid_name(self):
        kms = notion_kms.NotionKMS("tok")
        kms._client.pages.create.return_value = {"id": "p1"}
        kms.create_page("db1", "")
        props = kms._client.pages.create.call_args.kwargs["properties"]
        name = props["Name"]["title"][0]["text"]["content"]
        kind = props["Kind"]["select"]["name"]
        self.assertEqual(kind, "note")
        import uuid as _uuid
        try:
            _uuid.UUID(name)
            ok = True
        except ValueError:
            ok = False
        self.assertTrue(ok, f"name should be a UUID, got {name!r}")

    def test_08_create_page_kind_bootstrap(self):
        kms = notion_kms.NotionKMS("tok")
        kms._client.pages.create.return_value = {"id": "p1"}
        kms.create_page("db1", "boot", kind="bootstrap")
        props = kms._client.pages.create.call_args.kwargs["properties"]
        self.assertEqual(
            props["Name"]["title"][0]["text"]["content"], "__bootstrap__"
        )
        self.assertEqual(props["Kind"]["select"]["name"], "bootstrap")

    def test_09_search_filters_by_database_and_excludes_bootstrap(self):
        kms = notion_kms.NotionKMS("tok")
        kms._client.search.return_value = {
            "results": [
                {
                    "id": "p-good",
                    "parent": {"database_id": "db1"},
                    "properties": {
                        "Name": {
                            "title": [{"plain_text": "real note title"}]
                        },
                        "Body": {
                            "rich_text": [{"plain_text": "real body"}]
                        },
                        "Kind": {"select": {"name": "note"}},
                    },
                },
                {
                    "id": "p-wrong-db",
                    "parent": {"database_id": "OTHER"},
                    "properties": {
                        "Name": {"title": [{"plain_text": "x"}]},
                        "Body": {"rich_text": [{"plain_text": "x"}]},
                        "Kind": {"select": {"name": "note"}},
                    },
                },
                {
                    "id": "p-bootstrap",
                    "parent": {"database_id": "db1"},
                    "properties": {
                        "Name": {"title": [{"plain_text": "__bootstrap__"}]},
                        "Body": {"rich_text": [{"plain_text": "b"}]},
                        "Kind": {"select": {"name": "bootstrap"}},
                    },
                },
                {
                    "id": "p-bootstrap-by-name",
                    "parent": {"database_id": "db1"},
                    "properties": {
                        "Name": {"title": [{"plain_text": "__bootstrap__"}]},
                        "Body": {"rich_text": [{"plain_text": "b2"}]},
                        "Kind": {"select": {"name": "note"}},
                    },
                },
            ]
        }
        results = kms.search("db1", "query")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "p-good")
        self.assertEqual(results[0]["title"], "real note title")
        self.assertEqual(results[0]["body"], "real body")

    def test_10_get_bootstrap_returns_body_or_none(self):
        kms = notion_kms.NotionKMS("tok")

        kms._client.databases.query.return_value = {"results": []}
        self.assertIsNone(kms.get_bootstrap("db1"))

        kms._client.databases.query.return_value = {
            "results": [
                {
                    "id": "bp",
                    "properties": {
                        "Kind": {"select": {"name": "bootstrap"}},
                        "Body": {"rich_text": [{"plain_text": "boot-body"}]},
                    },
                }
            ]
        }
        self.assertEqual(kms.get_bootstrap("db1"), "boot-body")

    def test_11_set_bootstrap_creates_when_absent(self):
        kms = notion_kms.NotionKMS("tok")
        kms._client.databases.query.return_value = {"results": []}
        kms._client.pages.create.return_value = {"id": "new-boot"}
        pid = kms.set_bootstrap("db1", "body")
        self.assertEqual(pid, "new-boot")
        kms._client.pages.create.assert_called_once()
        kms._client.pages.update.assert_not_called()

    def test_12_set_bootstrap_updates_when_present(self):
        kms = notion_kms.NotionKMS("tok")
        kms._client.databases.query.return_value = {
            "results": [
                {
                    "id": "existing-boot",
                    "properties": {
                        "Kind": {"select": {"name": "bootstrap"}}
                    },
                }
            ]
        }
        kms._client.pages.update.return_value = {"id": "existing-boot"}
        pid = kms.set_bootstrap("db1", "new body")
        self.assertEqual(pid, "existing-boot")
        kms._client.pages.update.assert_called_once()
        kms._client.pages.create.assert_not_called()

    def test_13_repr_does_not_leak_token(self):
        kms = notion_kms.NotionKMS("super-secret-tok_xyz")
        self.assertNotIn("super-secret-tok_xyz", repr(kms))
        self.assertNotIn("super-secret-tok_xyz", str(kms))

    def test_14_import_is_lazy(self):
        import importlib
        import sys as _sys

        saved_kms = _sys.modules.pop("notion_kms", None)
        saved_client = _sys.modules.pop("notion_client", None)
        try:
            importlib.import_module("notion_kms")
            self.assertNotIn(
                "notion_client", _sys.modules,
                "notion_client must not be imported at module load",
            )
        finally:
            if saved_kms is not None:
                _sys.modules["notion_kms"] = saved_kms
            if saved_client is not None:
                _sys.modules["notion_client"] = saved_client


if __name__ == "__main__":
    unittest.main(verbosity=2)