"""Logging level helpers."""

from __future__ import annotations

import logging

from cells.log.env import warn_config_fallback

TRACE_LEVEL_NUM = 5
logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")


def parse_log_level(level: str | int) -> int:
    """Parse integer or case-insensitive level names, including TRACE (5).

    Standard aliases such as WARN/FATAL are accepted; unknown names warn and
    return INFO. Integers pass through unchanged, including custom levels
    registered by callers.
    """
    if isinstance(level, int):
        return level

    upper = str(level).strip().upper()
    if upper == "TRACE":
        return TRACE_LEVEL_NUM

    value = getattr(logging, upper, None)
    if isinstance(value, int):
        return value
    warn_config_fallback(
        "level",
        level,
        "INFO",
        allowed="TRACE, DEBUG, INFO, WARNING, WARN, ERROR, CRITICAL, FATAL, NOTSET, or an integer",
        risk="DEBUG and TRACE records may be filtered out",
    )
    return logging.INFO
