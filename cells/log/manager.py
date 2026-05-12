"""Lifecycle manager for cells-log."""

from __future__ import annotations

import atexit
import logging
import sys
from enum import IntEnum
from logging.handlers import QueueListener
from pathlib import Path
from threading import RLock

from cells.log.config import config_from_env, LogConfig
from cells.log.context import set_default_trace_id
from cells.log.formatters import ConsoleTextFormatter, ContextFilter, JsonLineFormatter
from cells.log.handlers import create_rotating_file_handler, LocalQueueHandler, make_log_queue, ModuleFileRouterHandler
from cells.log.levels import parse_log_level
from cells.log.logger import BoundLogger


class LogStage(IntEnum):
    UNINITIALIZED = 0
    BOOTSTRAP = 1
    PRODUCTION = 2
    SHUTDOWN = 3


class LogManager:
    """Bootstrap-safe logging manager.

    L0/bootstrap: console-only, no application settings required.
    L1/production: explicit LogConfig, optional module/total files, optional queue.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._handlers: list[logging.Handler] = []
        self._effective_handlers: list[logging.Handler] = []
        self._queue_listener: QueueListener | None = None
        self._queue_handler: LocalQueueHandler | None = None
        self._logger_cache: dict[str, logging.Logger] = {}
        self._config = LogConfig().normalized()
        self._level = logging.INFO
        self._stage = LogStage.UNINITIALIZED
        self.bootstrap()
        atexit.register(self.shutdown)

    @property
    def stage(self) -> LogStage:
        return self._stage

    def bootstrap(self, *, app_name: str = "app", env_prefix: str = "CELLS_LOG") -> None:
        """Configure console-only bootstrap logging from environment."""
        with self._lock:
            base = LogConfig(
                app_name=app_name,
                enable_console=True,
                enable_module_file=False,
                enable_total_file=False,
                use_queue=False,
            )
            config = config_from_env(app_name=app_name, env_prefix=env_prefix, base=base).normalized()
            self._replace_handlers(self._build_handlers(config, detail_sink_enabled=False), config, use_queue=False)
            self._stage = LogStage.BOOTSTRAP

    def configure(self, config: LogConfig, *, force: bool = True) -> None:
        """Configure production logging.

        force=True intentionally replaces the previous handler graph. This makes
        tests, CLI subcommands and settings reload deterministic.
        """
        with self._lock:
            if self._stage == LogStage.PRODUCTION and not force:
                return

            config = config.normalized()
            if (config.enable_module_file or config.enable_total_file) and config.log_dir is None:
                raise ValueError("config.log_dir is required when file logging is enabled")

            if config.log_dir is not None:
                Path(config.log_dir).mkdir(parents=True, exist_ok=True)

            detail_sink_enabled = config.enable_module_file or config.enable_total_file
            handlers = self._build_handlers(config, detail_sink_enabled=detail_sink_enabled)
            self._replace_handlers(handlers, config, use_queue=config.use_queue)
            self._stage = LogStage.PRODUCTION

    def configure_from_env(
        self,
        *,
        app_name: str = "app",
        default_log_dir: Path | str | None = None,
        env_prefix: str = "CELLS_LOG",
        base: LogConfig | None = None,
        force: bool = True,
    ) -> None:
        config = config_from_env(
            app_name=app_name,
            default_log_dir=default_log_dir,
            env_prefix=env_prefix,
            base=base,
        )
        self.configure(config, force=force)

    def get_logger(self, name: str | None = None) -> BoundLogger:
        with self._lock:
            logger_name = name or self._config.app_name
            raw_logger = self._logger_cache.get(logger_name)
            if raw_logger is None:
                raw_logger = logging.getLogger(logger_name)
                self._logger_cache[logger_name] = raw_logger

            self._sync_logger(raw_logger)
            return BoundLogger(
                raw_logger,
                {
                    "channel": logger_name,
                    "log_tag": logger_name.replace(".", "_"),
                },
            )

    def shutdown(self) -> None:
        with self._lock:
            if self._stage == LogStage.SHUTDOWN:
                return
            self._stop_queue_listener()
            for handler in self._handlers:
                try:
                    handler.flush()
                    handler.close()
                except Exception:
                    pass
            self._handlers.clear()
            self._effective_handlers.clear()
            self._queue_handler = None
            self._stage = LogStage.SHUTDOWN

    def _build_handlers(self, config: LogConfig, *, detail_sink_enabled: bool) -> list[logging.Handler]:
        level = parse_log_level(config.level)
        set_default_trace_id(config.default_trace_id)

        console_formatter: logging.Formatter
        file_formatter: logging.Formatter
        if config.format == "json":
            console_formatter = JsonLineFormatter(
                config,
                include_fields=config.console_include_fields,
                include_exception=config.console_include_exception,
            )
            file_formatter = JsonLineFormatter(config, include_fields=True, include_exception=True)
        else:
            console_formatter = ConsoleTextFormatter(
                config,
                include_fields=config.console_include_fields,
                include_exception=config.console_include_exception,
                simplify_console_errors=config.simplify_console_errors,
                detail_sink_enabled=detail_sink_enabled,
            )
            file_formatter = ConsoleTextFormatter(config, include_fields=True, include_exception=True)

        handlers: list[logging.Handler] = []
        context_filter = ContextFilter()

        if config.enable_console:
            stream = sys.stderr if config.console_to_stderr else sys.stdout
            console_handler = logging.StreamHandler(stream)
            console_handler.setLevel(level)
            console_handler.setFormatter(console_formatter)
            console_handler.addFilter(context_filter)
            handlers.append(console_handler)

        if config.enable_module_file:
            module_handler = ModuleFileRouterHandler(config, file_formatter, level)
            module_handler.addFilter(context_filter)
            handlers.append(module_handler)

        if config.enable_total_file:
            total_path = self._resolve_total_file(config)
            total_handler = create_rotating_file_handler(total_path, config, file_formatter, level)
            total_handler.addFilter(context_filter)
            handlers.append(total_handler)

        self._level = level
        return handlers

    def _resolve_total_file(self, config: LogConfig) -> Path:
        if config.log_dir is None:
            raise ValueError("config.log_dir is required for total file logging")

        if config.total_file is None:
            return Path(config.log_dir) / f"{config.app_name}.log"

        total_file = Path(config.total_file)
        if total_file.is_absolute():
            return total_file
        return Path(config.log_dir) / total_file

    def _replace_handlers(self, handlers: list[logging.Handler], config: LogConfig, *, use_queue: bool) -> None:
        self._stop_queue_listener()

        for old_handler in self._handlers:
            try:
                old_handler.flush()
                old_handler.close()
            except Exception:
                pass

        self._config = config
        self._handlers = handlers

        if use_queue and handlers:
            log_queue = make_log_queue()
            queue_handler = LocalQueueHandler(log_queue)
            queue_handler.setLevel(self._level)
            queue_handler.addFilter(ContextFilter())
            listener = QueueListener(log_queue, *handlers, respect_handler_level=True)
            listener.start()
            self._queue_handler = queue_handler
            self._queue_listener = listener
            self._effective_handlers = [queue_handler]
        else:
            self._queue_handler = None
            self._queue_listener = None
            self._effective_handlers = handlers

        logging.captureWarnings(config.capture_warnings)
        for raw_logger in self._logger_cache.values():
            self._sync_logger(raw_logger)

    def _sync_logger(self, raw_logger: logging.Logger) -> None:
        raw_logger.handlers.clear()
        raw_logger.setLevel(self._level)
        raw_logger.propagate = self._config.propagate
        for handler in self._effective_handlers:
            raw_logger.addHandler(handler)

    def _stop_queue_listener(self) -> None:
        if self._queue_listener is not None:
            try:
                self._queue_listener.stop()
            except Exception:
                pass
        self._queue_listener = None
        self._queue_handler = None


_MANAGER = LogManager()


def bootstrap_logging(*, app_name: str = "app", env_prefix: str = "CELLS_LOG") -> None:
    _MANAGER.bootstrap(app_name=app_name, env_prefix=env_prefix)


def configure_logging(config: LogConfig, *, force: bool = True) -> None:
    _MANAGER.configure(config, force=force)


def configure_logging_from_env(
    *,
    app_name: str = "app",
    default_log_dir: Path | str | None = None,
    env_prefix: str = "CELLS_LOG",
    base: LogConfig | None = None,
    force: bool = True,
) -> None:
    _MANAGER.configure_from_env(
        app_name=app_name,
        default_log_dir=default_log_dir,
        env_prefix=env_prefix,
        base=base,
        force=force,
    )


def get_logger(name: str | None = None) -> BoundLogger:
    return _MANAGER.get_logger(name)


def shutdown_logging() -> None:
    _MANAGER.shutdown()
