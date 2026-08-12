"""Bound structured logger wrapper."""

from __future__ import annotations

import logging
from typing import Any

from cells.log.levels import TRACE_LEVEL_NUM


class BoundLogger:
    """Small type-safe wrapper for structured logging.

    It keeps stdlib logging underneath, so libraries and existing handlers remain
    compatible while application code gets a stable structured API.
    """

    def __init__(self, logger: logging.Logger, context: dict[str, Any] | None = None) -> None:
        self._logger = logger
        self._context = dict(context or {})

    @property
    def raw_logger(self) -> logging.Logger:
        return self._logger

    def bind(self, **fields: Any) -> BoundLogger:
        return BoundLogger(self._logger, {**self._context, **fields})

    def trace(self, message: str, **fields: Any) -> None:
        self._log(TRACE_LEVEL_NUM, message, False, **fields)

    def debug(self, message: str, **fields: Any) -> None:
        self._log(logging.DEBUG, message, False, **fields)

    def info(self, message: str, **fields: Any) -> None:
        self._log(logging.INFO, message, False, **fields)

    def success(self, message: str, **fields: Any) -> None:
        self._log(logging.INFO, message, False, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._log(logging.WARNING, message, False, **fields)

    def warn(self, message: str, **fields: Any) -> None:
        self.warning(message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._log(logging.ERROR, message, False, **fields)

    def fail(self, message: str, **fields: Any) -> None:
        self._log(logging.ERROR, message, False, **fields)

    def critical(self, message: str, **fields: Any) -> None:
        self._log(logging.CRITICAL, message, False, **fields)

    def exception(self, message: str, **fields: Any) -> None:
        self._log(logging.ERROR, message, True, **fields)

    def _log(self, level: int, message: str, exc_info: bool, **fields: Any) -> None:
        merged_fields = {**self._context, **fields}
        self._logger.log(
            level,
            message,
            extra={"_fields": merged_fields},
            exc_info=exc_info,
            stacklevel=3,
        )
