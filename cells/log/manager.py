"""Lifecycle manager for cells-log."""

from __future__ import annotations

import atexit
import logging
import os
import sys
import weakref
import warnings
from dataclasses import dataclass, field, replace
from enum import IntEnum
from pathlib import Path
from threading import RLock

from cells.log.config import LogConfig, config_from_env
from cells.log.context import get_default_trace_id, set_default_trace_id
from cells.log.formatters import ConsoleTextFormatter, JsonLineFormatter
from cells.log.handlers import (
    LocalQueueHandler,
    MetricsQueueListener,
    ModuleFileRouterHandler,
    SafeQueueListener,
    SwitchingHandler,
    create_rotating_file_handler,
    make_log_queue,
    open_file_streams,
    release_file_streams,
)
from cells.log.levels import parse_log_level
from cells.log.logger import BoundLogger


class _LoggerState:
    """Snapshot of mutable stdlib logger state owned by another component."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.handlers = list(logger.handlers)
        self.level = logger.level
        self.propagate = logger.propagate
        self.disabled = logger.disabled

    def restore(self) -> None:
        """Restore the logger state captured at construction time."""
        self.logger.handlers[:] = self.handlers
        self.logger.setLevel(self.level)
        self.logger.propagate = self.propagate
        self.logger.disabled = self.disabled


class LogStage(IntEnum):
    """Represent the active lifecycle stage rather than candidate preparation progress.

    Preparation failure preserves the stage. Bootstrap or production configuration
    is allowed again after shutdown.
    """

    UNINITIALIZED = 0
    BOOTSTRAP = 1
    PRODUCTION = 2
    SHUTDOWN = 3


@dataclass(slots=True)
class _PreparedLoggingGraph:
    """Own candidate resources until activation transfers them or failure releases them.

    This is not another lifecycle stage. During preparation the manager retains
    the old graph and continues serving records.
    """

    # Normalized config and parsed level; preparation does not change the global trace fallback.
    config: LogConfig
    level: int
    # Actual sinks requiring flush/close; never close them while an old listener still uses them.
    handlers: list[logging.Handler] = field(default_factory=list)
    # Queue entrypoint and consumer; both are None in synchronous mode.
    queue_handler: LocalQueueHandler | None = None
    listener: SafeQueueListener | None = None

    @property
    def effective_handlers(self) -> tuple[logging.Handler, ...]:
        if self.queue_handler is not None:
            return (self.queue_handler,)
        return tuple(self.handlers)


_LIVE_MANAGERS: weakref.WeakSet[LogManager] = weakref.WeakSet()


def _reset_managers_after_fork() -> None:
    """Avoid dead queues and inherited locks in a forked child."""
    for manager in list(_LIVE_MANAGERS):
        manager._reset_after_fork()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_managers_after_fork)


class LogManager:
    """Bootstrap-safe logging manager.

    L0/bootstrap: console-only, no application settings required.
    L1/production: explicit LogConfig, optional module/total files, optional queue.
    """

    def __init__(self) -> None:
        """Create a stable entrypoint, bootstrap immediately and register exit cleanup."""
        self._lifecycle_lock = RLock()
        self._lock = RLock()
        self._switching_handler = SwitchingHandler()
        # Context is injected by SwitchingHandler.emit under the handover lock.
        self._handlers: list[logging.Handler] = []
        self._effective_handlers: list[logging.Handler] = []
        self._queue_listener: SafeQueueListener | None = None
        self._queue_handler: LocalQueueHandler | None = None
        self._retired_graphs: list[_PreparedLoggingGraph] = []
        self._logger_cache: dict[str, logging.Logger] = {}
        self._owned_logger_states: dict[str, _LoggerState] = {}
        self._config = LogConfig().normalized()
        self._root_state: _LoggerState | None = None
        self._warnings_state: _LoggerState | None = None
        self._warnings_capture_was_enabled = False
        self._level = logging.INFO
        self._stage = LogStage.UNINITIALIZED
        self.bootstrap()
        atexit.register(self.shutdown)
        _LIVE_MANAGERS.add(self)

    def _reset_after_fork(self) -> None:
        """Detach parent listeners/sinks; worker must configure its own graph.

        Do not join, stop, or close inherited queue/file resources here. The
        parent's background listener is absent and rotated files are not
        cross-process safe. Logging is intentionally muted until reconfigure.
        """
        self._lifecycle_lock = RLock()
        self._lock = RLock()
        self._switching_handler.createLock()
        self._switching_handler._handlers = ()
        self._handlers = []
        self._effective_handlers = []
        self._queue_handler = None
        self._queue_listener = None
        self._retired_graphs = []
        self._stage = LogStage.SHUTDOWN

    @property
    def stage(self) -> LogStage:
        """Return the active stage, unchanged while a candidate is being prepared."""
        return self._stage

    def bootstrap(self, *, app_name: str = "app", env_prefix: str = "CELLS_LOG") -> None:
        """Configure console-only bootstrap logging from environment."""
        with self._lifecycle_lock:
            base = LogConfig(
                app_name=app_name,
                enable_console=True,
                enable_module_file=False,
                enable_total_file=False,
                use_queue=False,
            )
            config = replace(
                config_from_env(app_name=app_name, env_prefix=env_prefix, base=base),
                enable_module_file=False,
                enable_total_file=False,
                use_queue=False,
                log_dir=None,
                total_file=None,
            )
            self._replace_handlers(config, stage=LogStage.BOOTSTRAP)

    def configure(self, config: LogConfig, *, force: bool = True) -> None:
        """Prepare and activate a config, then retire old resources.

        Preparation failure preserves the old config. force=False returns
        immediately if already in PRODUCTION, without comparing
        configuration values. Records remain served during candidate preparation
        and old-resource cleanup. Commit may drain the old queue and switch files;
        log calls wait for the switch lock, so handover is not zero-latency.

        Args:
            config: Console, file, queue and integration settings to activate.
            force: Replace an existing production config when True.

        Raises:
            ValueError: File logging is enabled without log_dir.
            OSError: Required directories or candidate file streams cannot open.
        """
        with self._lifecycle_lock:
            if self._stage == LogStage.PRODUCTION and not force:
                return

            config = config.normalized()
            if (config.enable_module_file or config.enable_total_file) and config.log_dir is None:
                raise ValueError("config.log_dir is required when file logging is enabled")

            if config.log_dir is not None:
                Path(config.log_dir).mkdir(parents=True, exist_ok=True)

            self._replace_handlers(config, stage=LogStage.PRODUCTION)

    def configure_from_env(
        self,
        *,
        app_name: str = "app",
        default_log_dir: Path | str | None = None,
        env_prefix: str = "CELLS_LOG",
        base: LogConfig | None = None,
        force: bool = True,
    ) -> None:
        """Activate environment overrides using the precedence in config_from_env()."""
        config = config_from_env(
            app_name=app_name,
            default_log_dir=default_log_dir,
            env_prefix=env_prefix,
            base=base,
        )
        self.configure(config, force=force)

    def get_logger(self, name: str | None = None) -> BoundLogger:
        """Cache the underlying logger and return a new wrapper with channel/log_tag fields."""
        with self._lock:
            logger_name = name or self._config.app_name
            raw_logger = self._logger_cache.get(logger_name)
            if raw_logger is None:
                raw_logger = logging.getLogger(logger_name)
                self._owned_logger_states[logger_name] = _LoggerState(raw_logger)
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
        """Detach routes, restore external state and drain resources.

        Retain resources whose cleanup failed for a later retry.
        """
        with self._lifecycle_lock:
            with self._lock, self._switching_handler.lock:
                if self._stage == LogStage.SHUTDOWN and not self._retired_graphs:
                    return
                old_graph = self._current_graph()
                self._switching_handler.replace_handlers(())
                self._handlers = []
                self._effective_handlers = []
                self._queue_listener = None
                self._queue_handler = None
                self._restore_external_loggers()
                for state in self._owned_logger_states.values():
                    state.restore()
                self._owned_logger_states.clear()
                self._logger_cache.clear()
                self._stage = LogStage.SHUTDOWN
                retired, self._retired_graphs = self._retired_graphs, []
            for graph in [*retired, old_graph]:
                self._dispose_graph(graph)

    def get_queue_stats(self) -> dict[str, int | bool | float]:
        """Snapshot queue delivery metrics (reset whenever configured again).

        Delivered means listener dispatch returned, not guaranteed durable write.
        ``max_depth_sampled`` is sampled, not a strict peak.  Sink handlers that
        swallow their own I/O errors may be invisible to ``delivery_errors``.
        """
        with self._lock:
            if self._queue_handler is None:
                return {
                    "enabled": False,
                    "capacity": 0,
                    "depth": 0,
                    "dropped_records": 0,
                    "accepted_records": 0,
                    "delivered_records": 0,
                    "fallback_records": 0,
                    "delivery_errors": 0,
                    "max_depth_sampled": 0,
                    "mean_delivery_latency_ms": 0.0,
                    "max_delivery_latency_ms": 0.0,
                }
            return self._queue_handler.stats()

    def _build_handlers(
        self,
        graph: _PreparedLoggingGraph,
        *,
        detail_sink_enabled: bool,
    ) -> None:
        config = graph.config
        handlers = graph.handlers
        level = graph.level

        console_formatter: logging.Formatter
        file_formatter: logging.Formatter
        if config.format == "json":
            console_formatter = JsonLineFormatter(
                config,
                include_fields=config.console_include_fields,
                include_exception=config.console_include_exception,
            )
            file_formatter = (
                console_formatter
                if config.console_include_fields and config.console_include_exception
                else JsonLineFormatter(config, include_fields=True, include_exception=True)
            )
        else:
            console_formatter = ConsoleTextFormatter(
                config,
                include_fields=config.console_include_fields,
                include_exception=config.console_include_exception,
                simplify_console_errors=config.simplify_console_errors,
                detail_sink_enabled=detail_sink_enabled,
            )
            file_formatter = ConsoleTextFormatter(
                config, include_fields=True, include_exception=True
            )

        if config.enable_console:
            stream = sys.stderr if config.console_to_stderr else sys.stdout
            console_handler = logging.StreamHandler(stream)
            handlers.append(console_handler)
            console_handler.setLevel(level)
            console_handler.setFormatter(console_formatter)

        if config.enable_module_file:
            module_handler = ModuleFileRouterHandler(config, file_formatter, level)
            handlers.append(module_handler)

        if config.enable_total_file:
            total_path = self._resolve_total_file(config)
            total_handler = create_rotating_file_handler(
                total_path, config, file_formatter, level, delay=True
            )
            handlers.append(total_handler)

    def _resolve_total_file(self, config: LogConfig) -> Path:
        if config.log_dir is None:
            raise ValueError("config.log_dir is required for total file logging")

        if config.total_file is None:
            return Path(config.log_dir) / f"{config.app_name}.log"

        total_file = Path(config.total_file)
        if total_file.is_absolute():
            return total_file
        return Path(config.log_dir) / total_file

    def _prepare_graph(self, config: LogConfig) -> _PreparedLoggingGraph:
        """Build and start an isolated candidate without changing live state."""
        graph = _PreparedLoggingGraph(config, parse_log_level(config.level))
        try:
            self._build_handlers(
                graph,
                detail_sink_enabled=config.enable_module_file or config.enable_total_file,
            )
            if config.use_queue and graph.handlers:
                log_queue = make_log_queue(config.queue_capacity)
                graph.queue_handler = LocalQueueHandler(
                    log_queue,
                    full_policy=config.queue_full_policy,
                    block_timeout=config.queue_block_timeout,
                    fallback_handlers=tuple(graph.handlers),
                )
                graph.queue_handler.setLevel(graph.level)
                graph.listener = MetricsQueueListener(
                    log_queue, *graph.handlers, metrics=graph.queue_handler
                )
                graph.listener.start()
            return graph
        except BaseException:
            self._dispose_graph(graph)
            raise

    def _current_graph(self) -> _PreparedLoggingGraph:
        return _PreparedLoggingGraph(
            self._config, self._level, self._handlers, self._queue_handler, self._queue_listener
        )

    def _replace_handlers(self, config: LogConfig, *, stage: LogStage) -> None:
        # Lifecycle calls serialize independently; old logging stays available
        # throughout candidate construction, listener startup and final cleanup.
        candidate = self._prepare_graph(config)
        try:
            with self._lock, self._switching_handler.lock:
                previous = self._current_graph()
                self._drain_queue()
                try:
                    open_file_streams(candidate.handlers)
                    release_file_streams(previous.handlers)
                    self._activate_graph(candidate, stage)
                except BaseException:
                    # Do not let an aborted candidate pin a file once old
                    # rotating writers resume. Preserve the triggering error.
                    try:
                        release_file_streams(candidate.handlers)
                    except BaseException as exc:
                        self._report_cleanup_failure("candidate file release", exc)
                    raise
        except BaseException:
            self._dispose_graph(candidate)
            raise
        self._dispose_graph(previous)

    def _publish_graph(self, graph: _PreparedLoggingGraph, stage: LogStage) -> None:
        """Publish manager state while callers hold both lifecycle locks."""
        self._config = graph.config
        self._level = graph.level
        self._handlers = graph.handlers
        self._queue_handler = graph.queue_handler
        self._queue_listener = graph.listener
        self._effective_handlers = [self._switching_handler] if graph.handlers else []
        self._stage = stage

    def _activate_graph(self, candidate: _PreparedLoggingGraph, stage: LogStage) -> None:
        """Apply routing changes or restore the exact preceding logger state."""
        loggers = {logging.getLogger(), logging.getLogger("py.warnings")}
        loggers.update(self._logger_cache.values())
        snapshots = [_LoggerState(logger) for logger in loggers]
        ownership = self._root_state, self._warnings_state, self._warnings_capture_was_enabled
        showwarning = warnings.showwarning
        capture_enabled = getattr(logging, "_warnings_showwarning", None) is not None
        previous = self._current_graph()
        previous_stage = self._stage
        default_trace_id = get_default_trace_id()
        try:
            self._configure_external_loggers(candidate.config, candidate.level)
            for raw_logger in self._logger_cache.values():
                self._sync_logger(raw_logger, config=candidate.config, level=candidate.level)
            self._switching_handler.replace_handlers(candidate.effective_handlers)
            self._publish_graph(candidate, stage)
            set_default_trace_id(candidate.config.default_trace_id)
        except BaseException:
            self._publish_graph(previous, previous_stage)
            self._switching_handler.replace_handlers(previous.effective_handlers)
            for snapshot in snapshots:
                snapshot.restore()
            logging.captureWarnings(capture_enabled)
            warnings.showwarning = showwarning
            self._root_state, self._warnings_state, self._warnings_capture_was_enabled = ownership
            set_default_trace_id(default_trace_id)
            raise

    def _sync_logger(
        self,
        raw_logger: logging.Logger,
        *,
        config: LogConfig | None = None,
        level: int | None = None,
    ) -> None:
        config = self._config if config is None else config
        raw_logger.setLevel(self._level if level is None else level)
        # Keep owned loggers on the stable entrypoint even when root ownership
        # changes; propagating as well would duplicate managed-root records.
        raw_logger.propagate = False if config.manage_root_logger else config.propagate
        raw_logger.handlers[:] = [self._switching_handler]

    def _configure_external_loggers(self, config: LogConfig, level: int) -> None:
        """Optionally route root and warnings records through cells-log."""
        if config.manage_root_logger:
            root_logger = logging.getLogger()
            if self._root_state is None:
                self._root_state = _LoggerState(root_logger)
            root_logger.handlers[:] = [self._switching_handler]
            root_logger.setLevel(level)
            root_logger.disabled = False
        elif self._root_state is not None:
            self._root_state.restore()
            self._root_state = None

        if config.capture_warnings:
            warnings_logger = logging.getLogger("py.warnings")
            if self._warnings_state is None:
                self._warnings_state = _LoggerState(warnings_logger)
                self._warnings_capture_was_enabled = (
                    getattr(logging, "_warnings_showwarning", None) is not None
                )
            logging.captureWarnings(True)
            warnings_logger.handlers[:] = [self._switching_handler]
            warnings_logger.propagate = False
            warnings_logger.setLevel(level)
            warnings_logger.disabled = False
        elif self._warnings_state is not None:
            self._warnings_state.restore()
            logging.captureWarnings(self._warnings_capture_was_enabled)
            self._warnings_state = None
            self._warnings_capture_was_enabled = False

    def _restore_external_loggers(self) -> None:
        """Return root and warnings loggers to their pre-cells state."""
        if self._warnings_state is not None:
            self._warnings_state.restore()
            logging.captureWarnings(self._warnings_capture_was_enabled)
            self._warnings_state = None
            self._warnings_capture_was_enabled = False
        if self._root_state is not None:
            self._root_state.restore()
            self._root_state = None

    def _drain_queue(self) -> None:
        """Wait for accepted records while the switching handler pauses producers."""
        if self._queue_handler is not None:
            self._queue_handler.queue.join()

    @staticmethod
    def _report_cleanup_failure(operation: str, exc: BaseException) -> None:
        """Report cleanup errors without replacing the original failure."""
        try:
            sys.stderr.write(f"cells-log: {operation} failed: {type(exc).__name__}: {exc}\n")
        except Exception:
            pass  # Diagnostics cannot undo activation or mask a preparation error.

    def _dispose_graph(self, graph: _PreparedLoggingGraph) -> None:
        """Release owned resources, retaining failed cleanup for shutdown retry."""
        if graph.listener is not None:
            try:
                graph.listener.stop()
            except BaseException as exc:
                self._report_cleanup_failure("listener cleanup", exc)
                self._retired_graphs.append(graph)
                return  # A listener must stop before its sinks can close.
            graph.listener = None
        if graph.queue_handler is not None:
            graph.handlers.append(graph.queue_handler)
            graph.queue_handler = None
        pending = []
        for handler in reversed(graph.handlers):
            try:
                handler.flush()
            except BaseException as exc:
                self._report_cleanup_failure("handler flush", exc)
            try:
                handler.close()
            except BaseException as exc:
                self._report_cleanup_failure("handler close", exc)
                pending.append(handler)
        graph.handlers[:] = reversed(pending)
        if pending:
            self._retired_graphs.append(graph)


_MANAGER = LogManager()


def bootstrap_logging(*, app_name: str = "app", env_prefix: str = "CELLS_LOG") -> None:
    """Enter console-only bootstrap using environment variables with the given prefix.

    Files and queueing are always disabled; the environment may disable the console
    too. Calling again also replaces production configuration. Call configure_logging()
    once the full application configuration is ready.
    """
    _MANAGER.bootstrap(app_name=app_name, env_prefix=env_prefix)


def configure_logging(config: LogConfig, *, force: bool = True) -> None:
    """Activate production config, retaining existing production config if force=False.

    Preparation failure preserves the old config; activation failure restores old
    routes. Log calls may wait during handover commit. Configuring does not turn
    the queue's drop policy into guaranteed delivery.
    """
    _MANAGER.configure(config, force=force)


def configure_logging_from_env(
    *,
    app_name: str = "app",
    default_log_dir: Path | str | None = None,
    env_prefix: str = "CELLS_LOG",
    base: LogConfig | None = None,
    force: bool = True,
) -> None:
    """Activate config_from_env() overrides with the force semantics of configure_logging.

    base supplies options without environment overrides; default_log_dir provides
    the base directory fallback. See config_from_env() for environment names,
    precedence and supported options.
    """
    _MANAGER.configure_from_env(
        app_name=app_name,
        default_log_dir=default_log_dir,
        env_prefix=env_prefix,
        base=base,
        force=force,
    )


def get_logger(name: str | None = None) -> BoundLogger:
    """Return a wrapper with channel/log_tag fields, defaulting the name to app_name.

    Existing wrappers follow configuration changes. Underlying loggers are cached
    by name, while wrappers are created on every call. Dynamic names grow the cache;
    put business IDs in fields rather than logger names.
    """
    return _MANAGER.get_logger(name)


def shutdown_logging() -> None:
    """Stop accepting records, drain the queue, close sinks and restore external state.

    Waiting has no fixed timeout, so slow sinks extend shutdown. Records previously
    discarded by a full queue are not recovered. Repeat to retry unfinished cleanup;
    logging may also be configured again afterward.
    """
    _MANAGER.shutdown()


def get_queue_stats() -> dict[str, int | bool | float]:
    """Return bounded-queue acceptance, delivery, loss and latency metrics."""
    return _MANAGER.get_queue_stats()
