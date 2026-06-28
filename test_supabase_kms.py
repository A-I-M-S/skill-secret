from __future__ import annotations

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import supabase_kms


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


class TestSupabaseKMS(unittest.TestCase):
    URL = "https://abc.supabase.co"
    KEY = "anon_key_abcdefgh"

    def setUp(self):
        self._mod, self._saved = _install_fake_supabase()

    def tearDown(self):
        _restore_supabase(self._saved)

    def test_01_whoami_returns_expected_keys(self):
        _FakeSupabaseClient.AUTH.get_user.return_value = {
            "user": {"id": "u-1"},
            "region": "us-west-1",
        }
        kms = supabase_kms.SupabaseKMS(self.URL, self.KEY)
        info = kms.whoami()
        self.assertEqual(info["project_url"], self.URL)
        self.assertEqual(info["anon_key_id"], self.KEY[:8] + "...")
        self.assertEqual(info["region"], "us-west-1")
        self.assertEqual(info["auth_status"], "authenticated")
        for v in info.values():
            self.assertNotIn(self.KEY, str(v))

    def test_02_ensure_schema_calls_select_1_from_notes(self):
        _FakeSupabaseClient.TABLE.select.return_value.limit.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "row-1"}])
        )
        kms = supabase_kms.SupabaseKMS(self.URL, self.KEY)
        kms.ensure_schema()
        _FakeSupabaseClient.TABLE.select.assert_called_once_with("id")
        kwargs = _FakeSupabaseClient.TABLE.select.return_value.limit.call_args
        self.assertEqual(kwargs.args, (1,))

    def test_03_ensure_schema_raises_when_table_missing(self):
        _FakeSupabaseClient.TABLE.select.return_value.limit.return_value.execute.side_effect = (
            Exception("schema not provisioned")
        )
        kms = supabase_kms.SupabaseKMS(self.URL, self.KEY)
        with self.assertRaises(supabase_kms.SupabaseKMSError) as ctx:
            kms.ensure_schema()
        self.assertIn("schema not provisioned", str(ctx.exception))

    def test_04_create_note_returns_id(self):
        _FakeSupabaseClient.TABLE.insert.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "new-row-id"}])
        )
        kms = supabase_kms.SupabaseKMS(self.URL, self.KEY)
        row_id = kms.create_note("hello world")
        self.assertEqual(row_id, "new-row-id")
        _FakeSupabaseClient.TABLE.insert.assert_called_once()
        row = _FakeSupabaseClient.TABLE.insert.call_args.args[0]
        self.assertEqual(row["body"], "hello world")
        self.assertEqual(row["kind"], "note")

    def test_05_create_note_truncates_title_at_80(self):
        _FakeSupabaseClient.TABLE.insert.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "row-1"}])
        )
        kms = supabase_kms.SupabaseKMS(self.URL, self.KEY)
        long_content = "a" * 200
        kms.create_note(long_content)
        row = _FakeSupabaseClient.TABLE.insert.call_args.args[0]
        self.assertEqual(len(row["title"]), 80)
        self.assertEqual(row["title"], "a" * 80)

    def test_06_create_note_empty_content_uses_uuid_title(self):
        _FakeSupabaseClient.TABLE.insert.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "row-1"}])
        )
        kms = supabase_kms.SupabaseKMS(self.URL, self.KEY)
        kms.create_note("")
        row = _FakeSupabaseClient.TABLE.insert.call_args.args[0]
        import uuid as _uuid
        try:
            _uuid.UUID(row["title"])
            ok = True
        except ValueError:
            ok = False
        self.assertTrue(ok, f"title should be a UUID, got {row['title']!r}")

    def test_07_create_note_kind_bootstrap_uses_sentinel_title(self):
        _FakeSupabaseClient.TABLE.insert.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "boot-row"}])
        )
        kms = supabase_kms.SupabaseKMS(self.URL, self.KEY)
        row_id = kms.create_note("boot-body", kind="bootstrap")
        self.assertEqual(row_id, "boot-row")
        row = _FakeSupabaseClient.TABLE.insert.call_args.args[0]
        self.assertEqual(row["title"], "__bootstrap__")
        self.assertEqual(row["kind"], "bootstrap")

    def test_08_search_returns_formatted_rows(self):
        _FakeSupabaseClient.RPC.execute.return_value = SimpleNamespace(
            data=[
                {"id": "r1", "title": "t1", "body": "b1", "rank": 0.9},
                {"id": "r2", "title": "t2", "body": "b2", "rank": 0.5},
            ]
        )
        kms = supabase_kms.SupabaseKMS(self.URL, self.KEY)
        results = kms.search("query text", limit=2)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], "r1")
        self.assertEqual(results[0]["title"], "t1")
        self.assertEqual(results[0]["body"], "b1")
        self.assertEqual(results[0]["rank"], 0.9)
        kms._client.rpc.assert_called_once_with(
            "search_notes",
            {"query_text": "query text", "max_results": 2},
        )

    def test_09_search_empty_data_returns_empty_list(self):
        _FakeSupabaseClient.RPC.execute.return_value = SimpleNamespace(data=None)
        kms = supabase_kms.SupabaseKMS(self.URL, self.KEY)
        self.assertEqual(kms.search("nothing", limit=1), [])

        _FakeSupabaseClient.RPC.execute.return_value = SimpleNamespace(data=[])
        self.assertEqual(kms.search("nothing", limit=1), [])

    def test_10_get_bootstrap_returns_body(self):
        _FakeSupabaseClient.TABLE.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
            SimpleNamespace(data=[{"body": "boot-body"}])
        )
        kms = supabase_kms.SupabaseKMS(self.URL, self.KEY)
        body = kms.get_bootstrap()
        self.assertEqual(body, "boot-body")

    def test_11_get_bootstrap_missing_returns_none(self):
        _FakeSupabaseClient.TABLE.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
            SimpleNamespace(data=[])
        )
        kms = supabase_kms.SupabaseKMS(self.URL, self.KEY)
        self.assertIsNone(kms.get_bootstrap())

    def test_12_set_bootstrap_deletes_then_inserts(self):
        _FakeSupabaseClient.TABLE.delete.return_value.eq.return_value.execute.return_value = (
            SimpleNamespace(data=[])
        )
        _FakeSupabaseClient.TABLE.insert.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "boot-new"}])
        )
        kms = supabase_kms.SupabaseKMS(self.URL, self.KEY)
        row_id = kms.set_bootstrap("new body")
        self.assertEqual(row_id, "boot-new")
        _FakeSupabaseClient.TABLE.delete.assert_called_once()
        eq_kwargs = _FakeSupabaseClient.TABLE.delete.return_value.eq.call_args
        self.assertEqual(eq_kwargs.args, ("kind", "bootstrap"))
        _FakeSupabaseClient.TABLE.insert.assert_called_once()

    def test_13_repr_does_not_leak_key(self):
        kms = supabase_kms.SupabaseKMS(self.URL, "super-secret-key_xyz")
        self.assertNotIn("super-secret-key_xyz", repr(kms))
        self.assertNotIn("super-secret-key_xyz", str(kms))

    def test_14_import_is_lazy(self):
        import importlib

        saved_kms = sys.modules.pop("supabase_kms", None)
        saved_supa = sys.modules.pop("supabase", None)
        try:
            importlib.import_module("supabase_kms")
            self.assertNotIn(
                "supabase", sys.modules,
                "supabase must not be imported at module load",
            )
        finally:
            if saved_kms is not None:
                sys.modules["supabase_kms"] = saved_kms
            if saved_supa is not None:
                sys.modules["supabase"] = saved_supa


if __name__ == "__main__":
    unittest.main(verbosity=2)