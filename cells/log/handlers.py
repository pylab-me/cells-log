"""Handler factories and routing handlers."""

from __future__ import annotations

import copy
import logging
import queue
import re
import time
from collections.abc import Iterable
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
from cells.log.formatters import ContextFilter

_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


class SwitchingHandler(logging.Handler):
    """Keep a stable logging entrypoint while the managed sinks change."""

    def __init__(self) -> None:
        """Initialize an empty handler group."""
        super().__init__()
        self._handlers: tuple[logging.Handler, ...] = ()
        self._context_filter = ContextFilter()

    def replace_handlers(self, handlers: tuple[logging.Handler, ...]) -> None:
        """Replace sinks under the same lock used for record delivery."""
        self.acquire()
        try:
            self._handlers = handlers
        finally:
            self.release()

    def emit(self, record: logging.LogRecord) -> None:
        """Deliver a record to the current sinks while handle() holds the lock."""
        if self._handlers:
            # Capture ContextVar once AFTER handover's switch lock is acquired.
            self._context_filter.filter(record)
        for handler in self._handlers:
            if record.levelno >= handler.level:
                handler.handle(record)


class LocalQueueHandler(QueueHandler):
    """QueueHandler variant for in-process logging.

    The stdlib QueueHandler.prepare() clears exc_info for pickle-safety. cells-log
    uses an optional in-process queue, so we keep a shallow copy of the record
    to preserve exception details for downstream file formatters.

    Only the record shell is copied; mutable field values are not frozen. Callers
    should avoid mutating submitted fields after enqueueing. LogConfig.queue_full_policy
    controls a full queue. sync_fallback writes in the caller and may overtake
    records waiting in the queue.
    """

    def __init__(
        self,
        log_queue: queue.Queue[logging.LogRecord],
        *,
        full_policy: str,
        block_timeout: float,
        fallback_handlers: tuple[logging.Handler, ...],
    ) -> None:
        """Bind the bounded queue, full policy and shared sinks without starting a listener."""
        super().__init__(log_queue)
        # The listener and sync_fallback share sinks rather than opening separate file handlers.
        self._full_policy = full_policy
        self._block_timeout = block_timeout
        self._fallback_handlers = fallback_handlers
        self._dropped_records = 0
        self._accepted_records = 0
        self._fallback_records = 0
        self._delivered_records = 0
        self._delivery_errors = 0
        self._queue_max_depth_sampled = 0
        self._total_delivery_latency_ns = 0
        self._max_delivery_latency_ns = 0
        self._counter_lock = RLock()

    @property
    def dropped_records(self) -> int:
        """Return the number of records discarded because the queue was full."""
        with self._counter_lock:
            return self._dropped_records

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        """Shallow-copy the record, preserving exc_info for the in-process listener."""
        return copy.copy(record)

    def enqueue(self, record: logging.LogRecord) -> None:
        """Enqueue a record according to the configured backpressure policy."""
        record._cells_enqueued_ns = time.perf_counter_ns()
        try:
            if self._full_policy == "block":
                self.queue.put(record, block=True, timeout=self._block_timeout)
            else:
                self.queue.put_nowait(record)
        except queue.Full:
            if self._full_policy == "sync_fallback":
                with self._counter_lock:
                    self._fallback_records += 1
                self._write_synchronously(record)
                return
            with self._counter_lock:
                self._dropped_records += 1
        else:
            with self._counter_lock:
                self._accepted_records += 1
                # Sample depth every 32 accepted events to avoid queue mutex
                # contention on every producer call. This is NOT a strict peak.
                if self._accepted_records % 32 == 0:
                    self._queue_max_depth_sampled = max(
                        self._queue_max_depth_sampled, self.queue.qsize()
                    )

    def delivery_finished(self, record: logging.LogRecord, *, failed: bool) -> None:
        """Track completed listener dispatch and elapsed queue + sink time."""
        latency_ns = time.perf_counter_ns() - record._cells_enqueued_ns
        with self._counter_lock:
            if failed:
                self._delivery_errors += 1
            else:
                self._delivered_records += 1
            self._total_delivery_latency_ns += latency_ns
            self._max_delivery_latency_ns = max(self._max_delivery_latency_ns, latency_ns)

    def stats(self) -> dict[str, int | bool | float]:
        """Return atomic queue submission and consumer delivery counters."""
        with self._counter_lock:
            completed = self._delivered_records + self._delivery_errors
            return {
                "enabled": True,
                "capacity": self.queue.maxsize,
                "depth": self.queue.qsize(),
                "dropped_records": self._dropped_records,
                "accepted_records": self._accepted_records,
                "delivered_records": self._delivered_records,
                "fallback_records": self._fallback_records,
                "delivery_errors": self._delivery_errors,
                "max_depth_sampled": self._queue_max_depth_sampled,
                "mean_delivery_latency_ms": (
                    round(self._total_delivery_latency_ns / completed / 1_000_000, 3)
                    if completed
                    else 0.0
                ),
                "max_delivery_latency_ms": round(self._max_delivery_latency_ns / 1_000_000, 3),
            }

    def _write_synchronously(self, record: logging.LogRecord) -> None:
        for handler in self._fallback_handlers:
            if record.levelno >= handler.level:
                handler.handle(record)


class SafeQueueListener(QueueListener):
    """QueueListener that can stop even while a bounded queue is full."""

    def enqueue_sentinel(self) -> None:
        """Wait for capacity to append the stop sentinel even when the queue is full."""
        self.queue.put(self._sentinel, block=True)

    def stop(self) -> None:
        """Allow cleanup when thread startup failed before a listener could run."""
        thread = self._thread
        if thread is None:
            return
        if thread.ident is None:
            self._thread = None
            return
        super().stop()


class MetricsQueueListener(SafeQueueListener):
    """Dispatch a queue and track visible completion/uncaught sink failures.

    Handler implementations that swallow errors via ``handleError`` cannot be
    measured here, and must expose their own transport-level health metrics.
    """

    def __init__(
        self,
        log_queue: queue.Queue[logging.LogRecord],
        *handlers: logging.Handler,
        metrics: LocalQueueHandler,
    ) -> None:
        super().__init__(log_queue, *handlers, respect_handler_level=True)
        self._metrics = metrics

    def handle(self, record: logging.LogRecord) -> None:
        try:
            super().handle(record)
        except Exception:
            self._metrics.delivery_finished(record, failed=True)
            # Keep accepting future events rather than killing the listener.
            self._metrics.handleError(record)
        else:
            self._metrics.delivery_finished(record, failed=False)


class ModuleFileRouterHandler(logging.Handler):
    """Route records to per-module files: a.b.c -> a_b_c.log.

    The router is bounded. If too many dynamic logger names are created, records
    are routed into overflow.log instead of opening unlimited file descriptors.

    Names outside the configured prefixes use external.log. Special route names
    are also sanitized, so filenames have no leading underscore. Handlers are
    cached by actual path: names resolving to the same sanitized path share a
    file while retaining their original logger names in records. At most one
    overflow handler is added beyond the cache limit.
    """

    def __init__(self, config: LogConfig, formatter: logging.Formatter, level: int) -> None:
        """Set the module directory and path cache, creating child handlers on first output."""
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
        """Route to a module file, delegating formatting and write failures to handleError."""
        try:
            module_name = self._route_name(record.name or self._config.app_name)
            handler = self._get_or_create_handler(module_name)
            handler.emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        """Close cached files and clear routes, continuing cleanup if an individual close fails."""
        with self._lock:
            for handler in self._handlers.values():
                with suppress(Exception):
                    handler.close()
            self._handlers.clear()
        super().close()

    def release_file_streams(self) -> None:
        """Unpin files during handover while preserving routing for rollback."""
        with self._lock:
            release_file_streams(self._handlers.values())

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
    *,
    delay: bool = False,
) -> logging.Handler:
    """Create a UTF-8 rotating handler with formatter, level and parent directory.

    delay=True defers opening the stream, but not creating its parent directory.
    Daily rotation uses local midnight; size rotation uses the parsed byte limit.
    The caller is responsible for closing the handler and its stream.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if config.rotation == "daily":
        handler: logging.Handler = TimedRotatingFileHandler(
            filename=path,
            when="midnight",
            interval=1,
            backupCount=config.retention,
            encoding="utf-8",
            delay=delay,
        )
    else:
        handler = RotatingFileHandler(
            filename=path,
            maxBytes=parse_size_limit(config.size_limit),
            backupCount=config.retention,
            encoding="utf-8",
            delay=delay,
        )

    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def open_file_streams(handlers: Iterable[logging.Handler]) -> None:
    """Open candidate files only after old rotating writes have drained."""
    for handler in handlers:
        if isinstance(handler, logging.FileHandler):
            with handler.lock:
                if handler.stream is None:
                    handler.stream = handler._open()
                    if isinstance(handler, TimedRotatingFileHandler):
                        # The old graph may have rotated since candidate construction.
                        modified = int(Path(handler.baseFilename).stat().st_mtime)
                        handler.rolloverAt = handler.computeRollover(modified)


def release_file_streams(handlers: Iterable[logging.Handler]) -> None:
    """Unpin retired files before publishing a replacement; allow lazy reopening."""
    for handler in handlers:
        if isinstance(handler, ModuleFileRouterHandler):
            handler.release_file_streams()
        elif isinstance(handler, logging.FileHandler):
            with handler.lock:
                handler.flush()
                if handler.stream is not None:
                    handler.stream.close()
                    handler.stream = None


def make_log_queue(capacity: int) -> queue.Queue[logging.LogRecord]:
    """Create a bounded in-process log queue."""
    return queue.Queue(maxsize=capacity)
