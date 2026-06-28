from __future__ import annotations

import uuid
from typing import Optional


_NAME_MAX = 80
_BOOTSTRAP_NAME = "__bootstrap__"


class NotionKMSError(Exception):
    pass


def _quote(text: str) -> str:
    if not text:
        return text
    return text if len(text) <= _NAME_MAX else text[:_NAME_MAX]


class NotionKMS:
    def __init__(self, token: str) -> None:
        from notion_client import Client  # noqa: WPS433 (lazy import, see design note)

        self._token = token
        self._client = Client(auth=token)

    def __repr__(self) -> str:
        return "NotionKMS(<redacted>)"

    def whoami(self) -> dict:
        try:
            resp = self._client.users.me()
        except Exception as exc:  # noqa: BLE001 — wrap any notion_client error
            raise NotionKMSError(f"whoami failed: {exc}") from exc

        bot = resp.get("bot", {}) if isinstance(resp, dict) else {}
        bot_id = bot.get("id") if isinstance(bot, dict) else None
        workspace_name = None
        workspace_id = None
        bot_workspace = (
            bot.get("workspace_name") if isinstance(bot, dict) else None
        )
        if isinstance(bot_workspace, dict):
            workspace_name = bot_workspace.get("name")
            workspace_id = bot_workspace.get("id")
        return {
            "bot_id": bot_id,
            "workspace_name": workspace_name,
            "workspace_id": workspace_id,
        }

    def ensure_database(
        self, parent_page_id: str, title: str = "skill-secret-vault"
    ) -> str:
        try:
            search_resp = self._client.search(
                query=title,
                filter={"property": "object", "value": "database"},
            )
        except Exception as exc:  # noqa: BLE001
            raise NotionKMSError(f"database search failed: {exc}") from exc

        for db in search_resp.get("results", []):
            db_title = _extract_db_title(db)
            if db_title == title:
                try:
                    fetched = self._client.databases.retrieve(db["id"])
                except Exception as exc:  # noqa: BLE001
                    raise NotionKMSError(
                        f"database retrieve failed: {exc}"
                    ) from exc
                if _database_parent_page_id(fetched) == parent_page_id:
                    return db["id"]

        properties = {
            "Name": {"title": {}},
            "Body": {"rich_text": {}},
            "Kind": {
                "select": {
                    "options": [
                        {"name": "note", "color": "default"},
                        {"name": "bootstrap", "color": "default"},
                    ]
                }
            },
        }
        try:
            created = self._client.databases.create(
                parent={"type": "page_id", "page_id": parent_page_id},
                title=[{"type": "text", "text": {"content": title}}],
                properties=properties,
            )
        except Exception as exc:  # noqa: BLE001
            raise NotionKMSError(f"database create failed: {exc}") from exc
        return created["id"]

    def create_page(self, db_id: str, content: str, *, kind: str = "note") -> str:
        if kind == "bootstrap":
            name = _BOOTSTRAP_NAME
        else:
            name = _quote(content) or str(uuid.uuid4())

        properties = {
            "Name": {
                "title": [{"type": "text", "text": {"content": name}}]
            },
            "Body": {
                "rich_text": [
                    {"type": "text", "text": {"content": content}}
                ]
            },
            "Kind": {"select": {"name": kind}},
        }
        try:
            page = self._client.pages.create(
                parent={"type": "database_id", "database_id": db_id},
                properties=properties,
            )
        except Exception as exc:  # noqa: BLE001
            raise NotionKMSError(f"page create failed: {exc}") from exc
        return page["id"]

    def search(self, db_id: str, query: str) -> list[dict]:
        try:
            resp = self._client.search(
                query=query,
                filter={"property": "object", "value": "page"},
            )
        except Exception as exc:  # noqa: BLE001
            raise NotionKMSError(f"search failed: {exc}") from exc

        results = []
        for page in resp.get("results", []):
            parent = page.get("parent", {}) or {}
            if parent.get("database_id") != db_id:
                continue
            title = _extract_page_title(page)
            if title == _BOOTSTRAP_NAME:
                continue
            kind = _extract_page_select(page, "Kind")
            if kind == "bootstrap":
                continue
            body = _extract_page_rich_text(page, "Body")
            results.append({"id": page["id"], "title": title, "body": body})
        return results

    def get_bootstrap(self, db_id: str) -> Optional[str]:
        page = _find_bootstrap_page(self._client, db_id)
        if page is None:
            return None
        return _extract_page_rich_text(page, "Body")

    def set_bootstrap(self, db_id: str, body: str) -> str:
        existing = _find_bootstrap_page(self._client, db_id)
        properties = {
            "Name": {
                "title": [
                    {"type": "text", "text": {"content": _BOOTSTRAP_NAME}}
                ]
            },
            "Body": {
                "rich_text": [
                    {"type": "text", "text": {"content": body}}
                ]
            },
            "Kind": {"select": {"name": "bootstrap"}},
        }
        if existing is None:
            try:
                page = self._client.pages.create(
                    parent={"type": "database_id", "database_id": db_id},
                    properties=properties,
                )
            except Exception as exc:  # noqa: BLE001
                raise NotionKMSError(
                    f"bootstrap create failed: {exc}"
                ) from exc
            return page["id"]
        try:
            page = self._client.pages.update(existing["id"], properties=properties)
        except Exception as exc:  # noqa: BLE001
            raise NotionKMSError(f"bootstrap update failed: {exc}") from exc
        return page["id"]


def _extract_db_title(db: dict) -> str:
    title_field = db.get("title", [])
    if not title_field:
        return ""
    return "".join(seg.get("plain_text", "") for seg in title_field)


def _database_parent_page_id(db: dict) -> str:
    parent = db.get("parent", {}) or {}
    return parent.get("page_id") or ""


def _extract_page_title(page: dict) -> str:
    properties = page.get("properties", {}) or {}
    name = properties.get("Name", {}) or {}
    title = name.get("title", [])
    return "".join(seg.get("plain_text", "") for seg in title)


def _extract_page_rich_text(page: dict, prop_name: str) -> str:
    properties = page.get("properties", {}) or {}
    prop = properties.get(prop_name, {}) or {}
    rich = prop.get("rich_text", [])
    return "".join(seg.get("plain_text", "") for seg in rich)


def _extract_page_select(page: dict, prop_name: str) -> str:
    properties = page.get("properties", {}) or {}
    prop = properties.get(prop_name, {}) or {}
    sel = prop.get("select")
    if not sel:
        return ""
    return sel.get("name", "")


def _find_bootstrap_page(client, db_id: str) -> Optional[dict]:
    try:
        resp = client.databases.query(
            database_id=db_id,
            filter={
                "property": "Name",
                "title": {"equals": _BOOTSTRAP_NAME},
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise NotionKMSError(f"bootstrap query failed: {exc}") from exc
    for page in resp.get("results", []):
        if _extract_page_select(page, "Kind") == "bootstrap":
            return page
    return None


__all__ = ["NotionKMS", "NotionKMSError"]