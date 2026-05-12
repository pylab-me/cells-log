"""Environment parsing utilities for cells-log."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}


def env_key(prefix: str, key: str) -> str:
    return f"{prefix}_{key}".upper()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def env_path(name: str, default: Path | None = None) -> Path | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return Path(raw.strip()).expanduser()


def parse_size_limit(raw_value: str | int) -> int:
    """Parse size like 100MB, 1GB, 512KB, 4096."""
    if isinstance(raw_value, int):
        return max(1, raw_value)

    value = str(raw_value).strip().upper()
    units = [
        ("GB", 1024 * 1024 * 1024),
        ("MB", 1024 * 1024),
        ("KB", 1024),
        ("B", 1),
    ]

    for unit, multiplier in units:
        if value.endswith(unit):
            number = value[: -len(unit)].strip()
            try:
                return max(1, int(number)) * multiplier
            except ValueError:
                return 100 * 1024 * 1024

    try:
        return max(1, int(value))
    except ValueError:
        return 100 * 1024 * 1024


def env_size(name: str, default: str | int) -> str | int:
    return os.getenv(name, str(default))


def coalesce(value: Any, fallback: Any) -> Any:
    return fallback if value is None else value
