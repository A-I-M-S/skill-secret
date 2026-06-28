"""Supabase-backed KMS adapter for skill-secret v4.

The v3 KMS stored secrets as Notion pages. v4 replaces that with a single
Supabase Postgres table (``notes``) plus a server-side full-text search
function (``search_notes``). One ``kind='bootstrap'`` row holds the
encrypted API-key blob; all other rows are ``kind='note'`` and hold
arbitrary secret payloads.

Schema setup is manual: ship ``_SCHEMA_SQL`` (also written verbatim to
``setup.sql`` at the repo root) and run it once in the Supabase SQL
editor. ``SupabaseKMS.ensure_schema()`` checks the table exists and
raises a clear ``SupabaseKMSError`` if it does not — we never auto-migrate.
"""

from __future__ import annotations

import uuid
from typing import Optional


_NAME_MAX = 80
_BOOTSTRAP_NAME = "__bootstrap__"


_SCHEMA_SQL = """
create table if not exists notes (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    body text not null,
    kind text not null default 'note' check (kind in ('note', 'bootstrap')),
    created_at timestamptz not null default now()
);

create index if not exists notes_kind_idx on notes (kind);

create or replace function search_notes(
    query_text text,
    max_results int
) returns table (
    id uuid,
    title text,
    body text,
    rank real
) language sql stable as $$
    select
        n.id,
        n.title,
        n.body,
        ts_rank(
            to_tsvector('english', n.title || ' ' || n.body),
            websearch_to_tsquery('english', query_text)
        ) as rank
    from notes n
    where
        n.kind = 'note'
        and to_tsvector('english', n.title || ' ' || n.body)
            @@ websearch_to_tsquery('english', query_text)
    order by rank desc
    limit max_results;
$$;
"""


class SupabaseKMSError(Exception):
    pass


def _quote(text: str) -> str:
    if not text:
        return text
    return text if len(text) <= _NAME_MAX else text[:_NAME_MAX]


class SupabaseKMS:
    def __init__(self, url: str, key: str) -> None:
        from supabase import Client, create_client  # noqa: WPS433 (lazy import, see design note)

        self._url = url
        self._key = key
        self._client: Client = create_client(url, key)

    def __repr__(self) -> str:
        return "SupabaseKMS(<redacted>)"

    def whoami(self) -> dict:
        try:
            resp = self._client.auth.get_user(jwt=self._key)
        except Exception as exc:  # noqa: BLE001 — wrap any supabase error
            raise SupabaseKMSError(f"whoami failed: {exc}") from exc

        envelope = resp if isinstance(resp, dict) else {}
        user = envelope.get("user") if isinstance(envelope, dict) else None
        anon_key_id = (self._key[:8] + "...") if self._key else ""

        region = None
        if isinstance(envelope, dict):
            region = envelope.get("region") or envelope.get("project_region")
        if not region:
            region = "unknown"

        auth_status = None
        if user is None:
            auth_status = "anonymous"
        else:
            auth_status = "authenticated"

        return {
            "project_url": self._url,
            "anon_key_id": anon_key_id,
            "region": region,
            "auth_status": auth_status,
        }

    def ensure_schema(self) -> None:
        try:
            resp = (
                self._client.table("notes")
                .select("id")
                .limit(1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            raise SupabaseKMSError(
                "schema not provisioned. Run the SQL in _SCHEMA_SQL once in "
                "your Supabase SQL editor, then re-run init."
            ) from exc
        data = getattr(resp, "data", None)
        if data is None:
            raise SupabaseKMSError(
                "schema not provisioned. Run the SQL in _SCHEMA_SQL once in "
                "your Supabase SQL editor, then re-run init."
            )

    def create_note(self, content: str, *, kind: str = "note") -> str:
        if kind == "bootstrap":
            title = _BOOTSTRAP_NAME
        else:
            title = _quote(content) or str(uuid.uuid4())

        row = {
            "title": title,
            "body": content,
            "kind": kind,
        }
        try:
            resp = self._client.table("notes").insert(row).execute()
        except Exception as exc:  # noqa: BLE001
            raise SupabaseKMSError(f"note insert failed: {exc}") from exc

        data = getattr(resp, "data", None)
        if not data:
            raise SupabaseKMSError(f"note insert returned no data: {resp!r}")
        first = data[0]
        if not isinstance(first, dict) or "id" not in first:
            raise SupabaseKMSError(
                f"note insert returned no id: {first!r}"
            )
        return str(first["id"])

    def search(self, query: str, *, limit: int = 1) -> list[dict]:
        try:
            resp = self._client.rpc(
                "search_notes",
                {"query_text": query, "max_results": limit},
            ).execute()
        except Exception as exc:  # noqa: BLE001
            raise SupabaseKMSError(f"search failed: {exc}") from exc

        data = getattr(resp, "data", None)
        if data is None:
            return []
        out: list[dict] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "id": row.get("id"),
                    "title": row.get("title", ""),
                    "body": row.get("body", ""),
                    "rank": row.get("rank", 0.0),
                }
            )
        return out

    def get_bootstrap(self) -> Optional[str]:
        try:
            resp = (
                self._client.table("notes")
                .select("body")
                .eq("kind", "bootstrap")
                .limit(1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            raise SupabaseKMSError(f"bootstrap read failed: {exc}") from exc

        data = getattr(resp, "data", None) or []
        if not data:
            return None
        first = data[0]
        if not isinstance(first, dict):
            return None
        body = first.get("body")
        return body if isinstance(body, str) else None

    def set_bootstrap(self, body: str) -> str:
        try:
            self._client.table("notes").delete().eq(
                "kind", "bootstrap"
            ).execute()
        except Exception as exc:  # noqa: BLE001
            raise SupabaseKMSError(
                f"bootstrap delete failed: {exc}"
            ) from exc

        return self.create_note(body, kind="bootstrap")


__all__ = ["SupabaseKMS", "SupabaseKMSError"]