"""cells-log public API."""

from .config import config_from_env, FieldProcessor, JsonDumps, LogConfig
from .context import (
    bind_log_context,
    get_log_context,
    get_trace_id,
    reset_log_context,
    reset_trace_id,
    set_trace_id,
)
from .levels import parse_log_level, TRACE_LEVEL_NUM
from .logger import BoundLogger
from .manager import (
    bootstrap_logging,
    configure_logging,
    configure_logging_from_env,
    get_logger,
    LogStage,
    shutdown_logging,
)


__all__ = [
    "BoundLogger",
    "FieldProcessor",
    "JsonDumps",
    "LogConfig",
    "LogStage",
    "TRACE_LEVEL_NUM",
    "bind_log_context",
    "bootstrap_logging",
    "config_from_env",
    "configure_logging",
    "configure_logging_from_env",
    "get_log_context",
    "get_logger",
    "get_trace_id",
    "parse_log_level",
    "reset_log_context",
    "reset_trace_id",
    "set_trace_id",
    "shutdown_logging",
]
