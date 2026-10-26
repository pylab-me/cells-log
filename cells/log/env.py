"""Environment parsing utilities for cells-log."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}


def warn_config_fallback(name: str, value: Any, fallback: Any, *, allowed: str, risk: str) -> None:
    """Report invalid configuration risks, accepted values and the actual fallback."""
    try:
        sys.stderr.write(
            f"cells-log: WARNING {name}={value!r} is invalid; "
            f"the effective value is {fallback!r}, so {risk}. "
            f"Valid values: {allowed}.\n"
        )
    except Exception:
        pass  # A diagnostic failure must not prevent the configured fallback.


def env_key(prefix: str, key: str) -> str:
    """Build an uppercase environment variable name such as CELLS_LOG_QUEUE_CAPACITY."""
    return f"{prefix}_{key}".upper()


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean, using the default when unset and warning on invalid values."""
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    warn_config_fallback(
        name,
        raw,
        default,
        allowed="true: 1, true, yes, y, on; false: 0, false, no, n, off (case-insensitive)",
        risk="the feature state may differ from the intended setting",
    )
    return default


def env_console_color(name: str, default: bool | str) -> bool | str:
    """Read console ANSI preference: false (default), auto, or always.

    A true-like value opts into auto TTY detection, rather than forcing ANSI.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _FALSE_VALUES or value == "never":
        return False
    if value in _TRUE_VALUES or value == "auto":
        return "auto"
    if value in {"always", "force"}:
        return "always"
    warn_config_fallback(
        name,
        raw,
        default,
        allowed="false/true, auto, always (also 0/1, no/yes, off/on)",
        risk="ANSI escapes may be emitted to an unintended consumer",
    )
    return default


def env_int(name: str, default: int) -> int:
    """Read an integer, warning and using the default if invalid; do not check ranges."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):  # fmt: skip
        warn_config_fallback(
            name,
            raw,
            default,
            allowed="an integer",
            risk="the numeric setting may differ from the intended limit",
        )
        return default


def env_path(name: str, default: Path | None = None) -> Path | None:
    """Expand ~ in a nonblank path or retain the default, without creating directories."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return Path(raw.strip()).expanduser()


def parse_size_limit(raw_value: str | int) -> int:
    """Parse integer bytes or B/KB/MB/GB strings using powers of 1024.

    Ignore case and surrounding whitespace. Clamp the numeric part to at least 1;
    fractions are unsupported. For example, 0MB becomes 1MB. Unparseable strings
    warn and fall back to 100MB.
    """
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
                warn_config_fallback(
                    "size_limit",
                    raw_value,
                    "100MB",
                    allowed="an integer byte count or an integer followed by B, KB, MB, GB",
                    risk="files may rotate at an unintended size threshold",
                )
                return 100 * 1024 * 1024

    try:
        return max(1, int(value))
    except ValueError:
        warn_config_fallback(
            "size_limit",
            raw_value,
            "100MB",
            allowed="an integer byte count or an integer followed by B, KB, MB, GB",
            risk="files may rotate at an unintended size threshold",
        )
        return 100 * 1024 * 1024


def env_size(name: str, default: str | int) -> str | int:
    """Read the raw size string; parsing is deferred until a size file handler is created."""
    return os.getenv(name, str(default))


def coalesce(value: Any, fallback: Any) -> Any:
    """Replace only None with the fallback, preserving False, zero and empty strings."""
    return fallback if value is None else value
