"""Logging level helpers."""

from __future__ import annotations

import logging


TRACE_LEVEL_NUM = 5
logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")


def parse_log_level(level: str | int) -> int:
    """Parse string/integer logging level with TRACE support."""
    if isinstance(level, int):
        return level

    upper = str(level).strip().upper()
    if upper == "TRACE":
        return TRACE_LEVEL_NUM

    value = getattr(logging, upper, None)
    return value if isinstance(value, int) else logging.INFO
