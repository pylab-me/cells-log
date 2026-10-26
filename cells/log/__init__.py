"""cells-log public API."""

from .config import FieldProcessor, JsonDumps, LogConfig, config_from_env
from .context import (
    bind_log_context,
    get_log_context,
    log_context,
    get_trace_id,
    reset_log_context,
    reset_trace_id,
    set_trace_id,
)
from .levels import TRACE_LEVEL_NUM, parse_log_level
from .logger import BoundLogger
from .manager import (
    LogStage,
    bootstrap_logging,
    configure_logging,
    configure_logging_from_env,
    get_logger,
    flush_logging,
    get_queue_stats,
    shutdown_logging,
)
from .timings import TimingCollector

__all__ = [
    "BoundLogger",
    "FieldProcessor",
    "JsonDumps",
    "LogConfig",
    "LogStage",
    "TRACE_LEVEL_NUM",
    "TimingCollector",
    "bind_log_context",
    "bootstrap_logging",
    "config_from_env",
    "configure_logging",
    "configure_logging_from_env",
    "get_log_context",
    "log_context",
    "get_logger",
    "flush_logging",
    "get_queue_stats",
    "get_trace_id",
    "parse_log_level",
    "reset_log_context",
    "reset_trace_id",
    "set_trace_id",
    "shutdown_logging",
]
