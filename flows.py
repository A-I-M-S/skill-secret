from __future__ import annotations

import base64
import os
import sys

import crypto
import envfile
import supabase_kms


ENV_VAR = "SKILL_SECRET_ENV"
ENV_KEY_BACKEND = "SKILL_SECRET_KMS_BACKEND"
ENV_KEY_PROJECT_URL = "SKILL_SECRET_KMS_PROJECT_URL"
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
        data = envfile.read(env_path, strict_v4=True)
    except FileNotFoundError:
        print("ERROR: Not initialized. Run init first.")
        sys.exit(4)
    except ValueError as exc:
        print(f"ERROR: .env is unreadable: {exc}")
        sys.exit(4)
    try:
        envfile.detect_backend(env_path)
    except ValueError as exc:
        print(f"ERROR: Unsupported backend: {exc}")
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

    url = args.url
    api_key = args.api_key
    password = args.password

    try:
        kms = supabase_kms.SupabaseKMS(url, api_key)
        kms.whoami()
    except supabase_kms.SupabaseKMSError as exc:
        print(f"ERROR: Supabase API rejected the api-key: {exc}")
        sys.exit(3)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Supabase API error: {exc}")
        sys.exit(3)

    try:
        kms.ensure_schema()
    except supabase_kms.SupabaseKMSError as exc:
        print(f"ERROR: Could not verify schema: {exc}")
        sys.exit(3)

    blob = crypto.encrypt_token(api_key, password)
    b64 = base64.b64encode(blob).decode("ascii")

    try:
        kms.set_bootstrap(b64)
    except supabase_kms.SupabaseKMSError as exc:
        print(f"ERROR: Could not write bootstrap: {exc}")
        sys.exit(3)

    try:
        envfile.write(env_path, {
            ENV_KEY_BACKEND: "supabase",
            ENV_KEY_PROJECT_URL: url,
            ENV_KEY_API_BLOB: b64,
        })
    except OSError as exc:
        print(f"ERROR: Could not write .env: {exc}")
        sys.exit(3)

    project_ref = url.split("//", 1)[-1].split(".", 1)[0]
    print(f"SUCCESS: KMS initialized. Database {project_ref} ({url}).")


def handle_take(args) -> None:
    env_path = _resolve_env_path(args)
    data = _load_env_or_exit(env_path)
    token = _decrypt_token_or_exit(data[ENV_KEY_API_BLOB], args.password)
    url = data[ENV_KEY_PROJECT_URL]

    try:
        kms = supabase_kms.SupabaseKMS(url, token)
        row_id = kms.create_note(args.content)
    except supabase_kms.SupabaseKMSError as exc:
        _err3("ERROR: Supabase API error", exc)
    except Exception as exc:  # noqa: BLE001
        _err3("ERROR: Supabase API error", exc)

    print(f"SUCCESS: Stored. Row id {row_id}.")


def handle_retrieve(args) -> None:
    env_path = _resolve_env_path(args)
    data = _load_env_or_exit(env_path)
    token = _decrypt_token_or_exit(data[ENV_KEY_API_BLOB], args.password)
    url = data[ENV_KEY_PROJECT_URL]

    sys.stderr.write("MODE: supabase\n")

    try:
        kms = supabase_kms.SupabaseKMS(url, token)
        results = kms.search(args.query, limit=1)
    except supabase_kms.SupabaseKMSError as exc:
        _err3("ERROR: Supabase API error", exc)
    except Exception as exc:  # noqa: BLE001
        _err3("ERROR: Supabase API error", exc)

    if not results:
        print("No highly relevant information found matching those parameters.")
        return

    top = results[0]
    print(f"--- MATCH FOUND ---\n{top['body']}")


def handle_whoami(args) -> None:
    env_path = _resolve_env_path(args)
    data = _load_env_or_exit(env_path)
    token = _decrypt_token_or_exit(data[ENV_KEY_API_BLOB], args.password)
    url = data[ENV_KEY_PROJECT_URL]

    try:
        kms = supabase_kms.SupabaseKMS(url, token)
        info = kms.whoami()
    except supabase_kms.SupabaseKMSError as exc:
        _err3("ERROR: Supabase API error", exc)
    except Exception as exc:  # noqa: BLE001
        _err3("ERROR: Supabase API error", exc)

    project_url = info.get("project_url") or url
    anon_key = info.get("anon_key_id") or ""
    print(f"--- ACCOUNT ---\nproject: {project_url}\nanon_key: {anon_key}")


__all__ = [
    "_resolve_env_path",
    "_load_env_or_exit",
    "handle_init",
    "handle_take",
    "handle_retrieve",
    "handle_whoami",
    "ENV_KEY_BACKEND",
    "ENV_KEY_PROJECT_URL",
    "ENV_KEY_API_BLOB",
]