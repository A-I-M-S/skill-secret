from __future__ import annotations

import os
from typing import Iterable


MODE_FILE = 0o600
MODE_DIR = 0o700


def read(path: str) -> dict:
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


__all__ = ["read", "write", "require_keys", "MODE_FILE", "MODE_DIR"]