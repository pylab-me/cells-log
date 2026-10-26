"""Formatters for structured and human-readable logs."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from cells.log.config import LogConfig
from cells.log.context import get_log_context, get_trace_id

UTC = timezone.utc


_COLORS = {
    "TRACE": "\033[35m",
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[41;37m",
    "RESET": "\033[0m",
}


class ContextFilter(logging.Filter):
    """Inject trace_id and context fields into LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Queue-based logging runs final handlers in a listener thread. Preserve
        # context values injected by the producer-side QueueHandler instead of
        # overwriting them with the listener thread's empty context.
        if not hasattr(record, "trace_id"):
            record.trace_id = get_trace_id()
        if not hasattr(record, "_context_fields"):
            record._context_fields = get_log_context()
        if not hasattr(record, "_fields"):
            record._fields = {}
        return True


# Internal LogRecord attributes and standard logging fields must not leak into
# third-party ``extra`` fields.  Calculated once, not on the formatting hot path.
_STANDARD_RECORD_KEYS = frozenset(logging.makeLogRecord({}).__dict__)


class _BaseCellsFormatter(logging.Formatter):
    def __init__(self, config: LogConfig, *, include_fields: bool, include_exception: bool) -> None:
        super().__init__()
        self._config = config
        self._include_fields = include_fields
        self._include_exception = include_exception

    def _timestamp(self, record: logging.LogRecord) -> str:
        if self._config.timestamp_utc:
            return datetime.fromtimestamp(record.created, UTC).isoformat()
        return datetime.fromtimestamp(record.created).isoformat()

    def _fields(self, record: logging.LogRecord) -> dict[str, Any]:
        # Share normalization among console, module and aggregate sinks.  A hook
        # must never have different outcomes for the same event.
        cached = getattr(record, "_cells_processed_fields", None)
        if cached is not None:
            return cached

        context_fields = getattr(record, "_context_fields", None) or {}
        bound_fields = getattr(record, "_fields", None) or {}
        # Standard third-party logging.Logger(..., extra={...}) is supported.
        # Never surface internal/private record properties or stdlib metadata.
        external_fields = (
            {}
            if getattr(record, "_cells_owned", False)
            else {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_RECORD_KEYS
                and not key.startswith("_")
                and key != "trace_id"
            }
        )
        fields = {**context_fields, **external_fields, **bound_fields}

        if self._config.field_processor is None:
            result = fields
        else:
            try:
                result = dict(self._config.field_processor(dict(fields)))
            except Exception as exc:
                # Fail closed: a failed redaction hook must NEVER return secrets.
                # Exclude the exception message; it too may contain secrets.
                result = {"_field_processor_error": type(exc).__name__}
        record._cells_processed_fields = result
        return result

    def _exception_text(self, record: logging.LogRecord) -> str | None:
        if not self._include_exception or not record.exc_info:
            return None
        return self.formatException(record.exc_info)


class JsonLineFormatter(_BaseCellsFormatter):
    """Production structured JSON line formatter."""

    def __init__(
        self, config: LogConfig, *, include_fields: bool = True, include_exception: bool = True
    ) -> None:
        super().__init__(config, include_fields=include_fields, include_exception=include_exception)

    def format(self, record: logging.LogRecord) -> str:
        cached = getattr(record, "_cells_json_render", None)
        if cached is not None and cached[0] is self:
            return cached[1]
        payload: dict[str, Any] = {
            "timestamp": self._timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", self._config.default_trace_id),
            "path": f"{record.pathname}:{record.lineno}",
            "func": record.funcName,
        }

        fields = self._fields(record)
        if self._include_fields and fields:
            payload["fields"] = fields

        exception = self._exception_text(record)
        if exception:
            payload["exception"] = exception

        try:
            result = self._config.json_dumps(payload, ensure_ascii=False)
        except TypeError:
            result = self._config.json_dumps(payload)
        record._cells_json_render = (self, result)
        return result


class ConsoleTextFormatter(_BaseCellsFormatter):
    """Development human-readable formatter."""

    def __init__(
        self,
        config: LogConfig,
        *,
        include_fields: bool = True,
        include_exception: bool = False,
        simplify_console_errors: bool = False,
        detail_sink_enabled: bool = False,
    ) -> None:
        super().__init__(config, include_fields=include_fields, include_exception=include_exception)
        self._simplify_console_errors = simplify_console_errors
        self._detail_sink_enabled = detail_sink_enabled

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self._timestamp(record)
        trace_id = getattr(record, "trace_id", self._config.default_trace_id)
        level_char = record.levelname[0]

        use_color = sys.stderr.isatty() and os.name != "nt"
        color = _COLORS.get(record.levelname, "") if use_color else ""
        reset = _COLORS["RESET"] if use_color else ""
        level_fmt = f"{color}[{level_char}]{reset}"

        if (
            self._simplify_console_errors
            and self._detail_sink_enabled
            and record.levelno >= logging.ERROR
        ):
            return f"{level_fmt} [{timestamp}] [{trace_id}] {record.name}: {record.getMessage()} (details in log file)"

        suffix = ""
        fields = self._fields(record)
        if self._include_fields and fields:
            try:
                suffix = f" | {self._config.json_dumps(fields, ensure_ascii=False)}"
            except TypeError:
                suffix = f" | {self._config.json_dumps(fields)}"

        message = (
            f"{level_fmt} [{timestamp}] [{trace_id}] {record.name}: {record.getMessage()}{suffix}"
        )
        exception = self._exception_text(record)
        if exception:
            message += "\n" + exception
        return message
