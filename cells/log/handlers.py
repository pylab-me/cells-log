"""Handler factories and routing handlers."""

from __future__ import annotations

import copy
import logging
import queue
import re
from logging.handlers import QueueHandler, RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from threading import RLock

from cells.log.config import LogConfig
from cells.log.env import parse_size_limit


_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


class LocalQueueHandler(QueueHandler):
    """QueueHandler variant for in-process logging.

    The stdlib QueueHandler.prepare() clears exc_info for pickle-safety. cells-log
    uses an in-process queue by default, so we keep a shallow copy of the record
    to preserve exception details for downstream file formatters.
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return copy.copy(record)


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
        self._base_dir = Path(config.log_dir)
        self._formatter = formatter
        self._level = level
        self._handlers: dict[str, logging.Handler] = {}
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
                try:
                    handler.close()
                except Exception:
                    pass
            self._handlers.clear()
        super().close()

    def _route_name(self, module_name: str) -> str:
        prefixes = self._config.module_name_prefixes
        if prefixes and not module_name.startswith(prefixes):
            return "_external"
        return module_name

    def _get_or_create_handler(self, module_name: str) -> logging.Handler:
        with self._lock:
            handler = self._handlers.get(module_name)
            if handler is not None:
                return handler

            if len(self._handlers) >= self._config.max_module_handlers:
                module_name = "_overflow"
                handler = self._handlers.get(module_name)
                if handler is not None:
                    return handler

            handler = self._create_handler(module_name)
            self._handlers[module_name] = handler
            return handler

    def _create_handler(self, module_name: str) -> logging.Handler:
        file_name = _sanitize_module_file_name(module_name)
        log_path = self._base_dir / self._config.module_dir_name / f"{file_name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = create_rotating_file_handler(log_path, self._config, self._formatter, self._level)
        return handler


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


def make_log_queue() -> queue.SimpleQueue[logging.LogRecord]:
    return queue.SimpleQueue()
