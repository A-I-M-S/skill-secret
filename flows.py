from __future__ import annotations

import base64
import os
import sys

import crypto
import envfile
import notion_kms


ENV_VAR = "SKILL_SECRET_ENV"
ENV_KEY_DB_ID = "SKILL_SECRET_KMS_DB_ID"
ENV_KEY_PARENT_PAGE_ID = "SKILL_SECRET_KMS_PARENT_PAGE_ID"
ENV_KEY_API_BLOB = "SKILL_SECRET_KMS_API_BLOB"


def _resolve_env_path(args) -> str:
    flag_value = getattr(args, "env_file", None)
    if flag_value:
        return flag_value
    env_value = os.environ.get(ENV_VAR)
    if env_value:
        return env_value
    return os.path.join(os.getcwd(), ".env")


def _load_env_or_exit(env_path: str) -> dict:
    try:
        data = envfile.read(env_path)
    except FileNotFoundError:
        print("ERROR: Not initialized. Run init first.")
        sys.exit(4)
    except ValueError as exc:
        print(f"ERROR: .env is unreadable: {exc}")
        sys.exit(4)
    try:
        envfile.require_keys(
            env_path,
            [ENV_KEY_DB_ID, ENV_KEY_PARENT_PAGE_ID, ENV_KEY_API_BLOB],
        )
    except ValueError as exc:
        print(f"ERROR: .env is unreadable: {exc}")
        sys.exit(4)
    return data


def _decrypt_token_or_exit(blob_b64: str, password: str) -> str:
    try:
        blob = base64.b64decode(blob_b64)
    except Exception as exc:
        print(f"ERROR: .env is unreadable: {exc}")
        sys.exit(4)
    try:
        return crypto.decrypt_token(blob, password)
    except crypto.InvalidToken:
        print("ERROR: Wrong password.")
        sys.exit(2)


def _err3(prefix: str, exc: Exception) -> None:
    print(f"{prefix}: {exc}")
    sys.exit(3)


def handle_init(args) -> None:
    env_path = _resolve_env_path(args)
    if os.path.exists(env_path):
        print("ERROR: Already initialized. Delete .env to re-init.")
        sys.exit(5)

    notion_token = args.notion_token
    password = args.password
    parent_page_id = args.parent_page_id

    try:
        kms = notion_kms.NotionKMS(notion_token)
        try:
            kms.whoami()
        except notion_kms.NotionKMSError as exc:
            print(f"ERROR: Notion API rejected the token: {exc}")
            sys.exit(3)
    except notion_kms.NotionKMSError as exc:
        print(f"ERROR: Notion API error: {exc}")
        sys.exit(3)

    try:
        db_id = kms.ensure_database(parent_page_id, title="skill-secret-vault")
    except notion_kms.NotionKMSError as exc:
        print(f"ERROR: Could not create database: {exc}")
        sys.exit(3)

    blob = crypto.encrypt_token(notion_token, password)
    b64 = base64.b64encode(blob).decode("ascii")

    try:
        kms.set_bootstrap(db_id, b64)
    except notion_kms.NotionKMSError as exc:
        print(f"ERROR: Could not write bootstrap: {exc}")
        sys.exit(3)

    try:
        envfile.write(env_path, {
            ENV_KEY_DB_ID: db_id,
            ENV_KEY_PARENT_PAGE_ID: parent_page_id,
            ENV_KEY_API_BLOB: b64,
        })
    except OSError as exc:
        print(f"ERROR: Could not write .env: {exc}")
        sys.exit(3)

    print(f"SUCCESS: KMS initialized. Database skill-secret-vault ({db_id}).")


def handle_take(args) -> None:
    env_path = _resolve_env_path(args)
    data = _load_env_or_exit(env_path)
    token = _decrypt_token_or_exit(data[ENV_KEY_API_BLOB], args.password)
    db_id = data[ENV_KEY_DB_ID]

    try:
        kms = notion_kms.NotionKMS(token)
        page_id = kms.create_page(db_id, args.content)
    except notion_kms.NotionKMSError as exc:
        _err3("ERROR: Notion API error", exc)

    print(f"SUCCESS: Stored. Page id {page_id}.")


def handle_retrieve(args) -> None:
    env_path = _resolve_env_path(args)
    data = _load_env_or_exit(env_path)
    token = _decrypt_token_or_exit(data[ENV_KEY_API_BLOB], args.password)
    db_id = data[ENV_KEY_DB_ID]

    sys.stderr.write("MODE: notion\n")

    try:
        kms = notion_kms.NotionKMS(token)
        results = kms.search(db_id, args.query)
    except notion_kms.NotionKMSError as exc:
        _err3("ERROR: Notion API error", exc)

    if not results:
        print("No highly relevant information found matching those parameters.")
        return

    top = results[0]
    print(f"--- MATCH FOUND ---\n{top['body']}")


def handle_whoami(args) -> None:
    env_path = _resolve_env_path(args)
    data = _load_env_or_exit(env_path)
    token = _decrypt_token_or_exit(data[ENV_KEY_API_BLOB], args.password)

    try:
        kms = notion_kms.NotionKMS(token)
        info = kms.whoami()
    except notion_kms.NotionKMSError as exc:
        _err3("ERROR: Notion API error", exc)

    bot_id = info.get("bot_id") or ""
    workspace = info.get("workspace_name") or ""
    print(f"--- ACCOUNT ---\nbot_id: {bot_id}\nworkspace: {workspace}")


__all__ = [
    "_resolve_env_path",
    "_load_env_or_exit",
    "handle_init",
    "handle_take",
    "handle_retrieve",
    "handle_whoami",
    "ENV_KEY_DB_ID",
    "ENV_KEY_PARENT_PAGE_ID",
    "ENV_KEY_API_BLOB",
]
