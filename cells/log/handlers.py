"""Handler factories and routing handlers."""

from __future__ import annotations

import copy
import logging
import queue
import re
from contextlib import suppress
from logging.handlers import (
    QueueHandler,
    QueueListener,
    RotatingFileHandler,
    TimedRotatingFileHandler,
)
from pathlib import Path
from threading import RLock

from cells.log.config import LogConfig
from cells.log.env import parse_size_limit


_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


class SwitchingHandler(logging.Handler):
    """Keep a stable logging entrypoint while the managed sinks change."""

    def __init__(self) -> None:
        """Initialize an empty handler group."""
        super().__init__()
        self._handlers: tuple[logging.Handler, ...] = ()

    def replace_handlers(self, handlers: tuple[logging.Handler, ...]) -> None:
        """Replace sinks under the same lock used for record delivery."""
        self.acquire()
        try:
            self._handlers = handlers
        finally:
            self.release()

    def emit(self, record: logging.LogRecord) -> None:
        """Deliver a record to the current sinks while handle() holds the lock."""
        for handler in self._handlers:
            if record.levelno >= handler.level:
                handler.handle(record)


class LocalQueueHandler(QueueHandler):
    """QueueHandler variant for in-process logging.

    The stdlib QueueHandler.prepare() clears exc_info for pickle-safety. cells-log
    uses an in-process queue by default, so we keep a shallow copy of the record
    to preserve exception details for downstream file formatters.
    """

    def __init__(
        self,
        log_queue: queue.Queue[logging.LogRecord],
        *,
        full_policy: str,
        block_timeout: float,
        fallback_handlers: tuple[logging.Handler, ...],
    ) -> None:
        super().__init__(log_queue)
        self._full_policy = full_policy
        self._block_timeout = block_timeout
        self._fallback_handlers = fallback_handlers
        self._dropped_records = 0
        self._counter_lock = RLock()

    @property
    def dropped_records(self) -> int:
        """Return the number of records discarded because the queue was full."""
        with self._counter_lock:
            return self._dropped_records

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return copy.copy(record)

    def enqueue(self, record: logging.LogRecord) -> None:
        """Enqueue a record according to the configured backpressure policy."""
        try:
            if self._full_policy == "block":
                self.queue.put(record, block=True, timeout=self._block_timeout)
            else:
                self.queue.put_nowait(record)
        except queue.Full:
            if self._full_policy == "sync_fallback":
                self._write_synchronously(record)
                return
            with self._counter_lock:
                self._dropped_records += 1

    def _write_synchronously(self, record: logging.LogRecord) -> None:
        for handler in self._fallback_handlers:
            if record.levelno >= handler.level:
                handler.handle(record)


class SafeQueueListener(QueueListener):
    """QueueListener that can stop even while a bounded queue is full."""

    def enqueue_sentinel(self) -> None:
        self.queue.put(self._sentinel, block=True)


class ModuleFileRouterHandler(logging.Handler):
    """Route records to per-module files: a.b.c -> a_b_c.log.

    The router is bounded. If too many dynamic logger names are created, records
    are routed into _overflow.log instead of opening unlimited file descriptors.
    """

    def __init__(self, config: LogConfig, formatter: logging.Formatter, level: int) -> None:
        super().__init__(level)
        if config.log_dir is None:
            raise ValueError("ModuleFileRouterHandler requires config.log_dir")

        self._config = config
        self._module_dir = (Path(config.log_dir) / config.module_dir_name).resolve()
        self._formatter = formatter
        self._level = level
        self._handlers: dict[Path, logging.Handler] = {}
        self._lock = RLock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            module_name = self._route_name(record.name or self._config.app_name)
            handler = self._get_or_create_handler(module_name)
            handler.emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self._lock:
            for handler in self._handlers.values():
                with suppress(Exception):
                    handler.close()
            self._handlers.clear()
        super().close()

    def _route_name(self, module_name: str) -> str:
        prefixes = self._config.module_name_prefixes
        if prefixes and not module_name.startswith(prefixes):
            return "_external"
        return module_name

    def _get_or_create_handler(self, module_name: str) -> logging.Handler:
        with self._lock:
            log_path = self._module_path(module_name)
            handler = self._handlers.get(log_path)
            if handler is not None:
                return handler

            if len(self._handlers) >= self._config.max_module_handlers:
                log_path = self._module_path("_overflow")
                handler = self._handlers.get(log_path)
                if handler is not None:
                    return handler

            handler = create_rotating_file_handler(
                log_path, self._config, self._formatter, self._level
            )
            self._handlers[log_path] = handler
            return handler

    def _module_path(self, module_name: str) -> Path:
        file_name = _sanitize_module_file_name(module_name)
        return self._module_dir / f"{file_name}.log"


def _sanitize_module_file_name(module_name: str) -> str:
    cleaned = _SAFE_NAME_PATTERN.sub("_", module_name.strip().replace(".", "_"))
    cleaned = cleaned.strip("._-")
    return cleaned or "app"


def create_rotating_file_handler(
    path: Path,
    config: LogConfig,
    formatter: logging.Formatter,
    level: int,
) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)

    if config.rotation == "daily":
        handler: logging.Handler = TimedRotatingFileHandler(
            filename=path,
            when="midnight",
            interval=1,
            backupCount=config.retention,
            encoding="utf-8",
        )
    else:
        handler = RotatingFileHandler(
            filename=path,
            maxBytes=parse_size_limit(config.size_limit),
            backupCount=config.retention,
            encoding="utf-8",
        )

    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def make_log_queue(capacity: int) -> queue.Queue[logging.LogRecord]:
    """Create a bounded in-process log queue."""
    return queue.Queue(maxsize=capacity)
