from __future__ import annotations

import os
from typing import Iterable


MODE_FILE = 0o600
MODE_DIR = 0o700


def read(path: str, *, strict_v4: bool = False) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    data: dict = {}
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n").rstrip("\r")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parsed = _parse_line(stripped, lineno)
            if parsed is None:
                continue
            key, value = parsed
            if key in data:
                raise ValueError(
                    f"duplicate key in {path}: {key} at line {lineno}"
                )
            data[key] = value

    if strict_v4:
        has_v3_keys = (
            "SKILL_SECRET_KMS_DB_ID" in data
            or "SKILL_SECRET_KMS_PARENT_PAGE_ID" in data
        )
        has_backend = "SKILL_SECRET_KMS_BACKEND" in data
        if has_v3_keys and not has_backend:
            raise ValueError(
                "v3 .env detected (SKILL_SECRET_KMS_DB_ID or "
                "SKILL_SECRET_KMS_PARENT_PAGE_ID present without "
                "SKILL_SECRET_KMS_BACKEND). Migrate to v4 by running "
                "`secret init` against a Supabase project — see "
                "CHANGELOG.md."
            )

    return data


def write(path: str, data: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, mode=MODE_DIR, exist_ok=True)
        os.chmod(parent, MODE_DIR)

    payload = _serialize(data).encode("utf-8")
    tmp = path + ".tmp"

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, MODE_FILE)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    os.replace(tmp, path)


def require_keys(path: str, keys: Iterable[str]) -> dict:
    data = read(path)
    for key in keys:
        if key not in data:
            raise ValueError(f"missing required key: {key}")
    return data


def detect_backend(path: str) -> str:
    data = read(path, strict_v4=True)
    backend = data.get("SKILL_SECRET_KMS_BACKEND")
    if backend != "supabase":
        raise ValueError(
            f"unknown or missing SKILL_SECRET_KMS_BACKEND: {backend!r}. "
            "v4 only supports backend=supabase."
        )
    project_url = data.get("SKILL_SECRET_KMS_PROJECT_URL")
    api_blob = data.get("SKILL_SECRET_KMS_API_BLOB")
    if not project_url or not api_blob:
        raise ValueError(
            "missing required v4 keys: SKILL_SECRET_KMS_PROJECT_URL and "
            "SKILL_SECRET_KMS_API_BLOB must both be set."
        )
    return "supabase"


def _parse_line(line: str, lineno: int) -> tuple | None:
    if "=" not in line:
        raise ValueError(f"malformed .env line {lineno}: {line!r}")
    key, _, value = line.partition("=")
    key = key.strip()
    if not key:
        raise ValueError(f"empty key on line {lineno}: {line!r}")
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return key, value


def _serialize(data: dict) -> str:
    lines = []
    for key in sorted(data):
        lines.append(f"{key}={data[key]}\n")
    return "".join(lines)


__all__ = ["read", "write", "require_keys", "detect_backend", "MODE_FILE", "MODE_DIR"]