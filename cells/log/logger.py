"""Bound structured logger wrapper."""

from __future__ import annotations

import logging
from typing import Any

from cells.log.levels import TRACE_LEVEL_NUM


class BoundLogger:
    """Provide bound fields and structured logging over a standard library logger.

    Level methods accept a message and **fields, without stdlib-style positional
    formatting arguments. Keywords such as level and exc_info are business fields;
    they do not control severity or exception capture. Field precedence is
    context < bind < call. Use exception() to capture the current exception;
    success()/fail() are aliases for INFO/ERROR respectively.
    """

    def __init__(self, logger: logging.Logger, context: dict[str, Any] | None = None) -> None:
        """Wrap an existing logger and shallow-copy fields without owning its lifecycle."""
        self._logger = logger
        self._context = dict(context or {})

    @property
    def raw_logger(self) -> logging.Logger:
        """Return the underlying logger; changing its handlers affects manager routing."""
        return self._logger

    def bind(self, **fields: Any) -> BoundLogger:
        """Return a new wrapper with merged fields; nested field values remain shared."""
        return BoundLogger(self._logger, {**self._context, **fields})

    def trace(self, message: str, **fields: Any) -> None:
        """Log fine-grained diagnostics at TRACE (5), below DEBUG."""
        self._log(TRACE_LEVEL_NUM, message, False, **fields)

    def debug(self, message: str, **fields: Any) -> None:
        """Log the message and business fields at DEBUG."""
        self._log(logging.DEBUG, message, False, **fields)

    def info(self, message: str, **fields: Any) -> None:
        """Log the message and business fields at INFO."""
        self._log(logging.INFO, message, False, **fields)

    def success(self, message: str, **fields: Any) -> None:
        """Log a success event at INFO without registering a separate SUCCESS level."""
        self._log(logging.INFO, message, False, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        """Log an event requiring attention and its business fields at WARNING."""
        self._log(logging.WARNING, message, False, **fields)

    def warn(self, message: str, **fields: Any) -> None:
        """Provide a compatibility alias for warning()."""
        self.warning(message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        """Log at ERROR; use exception() to include the current traceback."""
        self._log(logging.ERROR, message, False, **fields)

    def fail(self, message: str, **fields: Any) -> None:
        """Log a failure at ERROR without automatically attaching a traceback."""
        self._log(logging.ERROR, message, False, **fields)

    def critical(self, message: str, **fields: Any) -> None:
        """Log a severe failure at CRITICAL without terminating the process."""
        self._log(logging.CRITICAL, message, False, **fields)

    def exception(self, message: str, **fields: Any) -> None:
        """Log at ERROR with the current exception, normally from an except block."""
        self._log(logging.ERROR, message, True, **fields)

    def _log(self, _level: int, _message: str, _exc_info: bool, /, **fields: Any) -> None:
        # Private positional-only parameters leave even identically named business fields intact.
        merged_fields = {**self._context, **fields}
        self._logger.log(
            _level,
            _message,
            extra={"_fields": merged_fields},
            exc_info=_exc_info,
            stacklevel=3,
        )
