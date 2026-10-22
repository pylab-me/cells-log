"""Configuration model for cells-log."""

from __future__ import annotations

import json as std_json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from cells.log.env import (
    env_bool,
    env_int,
    env_key,
    env_path,
    env_size,
    warn_config_fallback,
)

LogFormat = Literal["json", "text"]
RotationMode = Literal["daily", "size"]
QueueFullPolicy = Literal["drop", "block", "sync_fallback"]
# Transform merged structured fields without processing the message or exception text.
FieldProcessor = Callable[[dict[str, Any]], Mapping[str, Any] | dict[str, Any]]
# Return str; accept either the ensure_ascii keyword or just the object to serialize.
JsonDumps = Callable[..., str]


def _default_json_dumps(obj: Any, **kwargs: Any) -> str:
    """Default JSON dumper with safe fallback for non-primitive objects."""
    kwargs.setdefault("ensure_ascii", False)
    kwargs.setdefault("default", str)
    return std_json.dumps(obj, **kwargs)


@dataclass(slots=True)
class LogConfig:
    """Configure output, file rotation, queueing and context extensions.

    Defaults apply to explicit production configuration: console JSON with an
    in-process queue, no files, no root ownership and no warnings capture.
    Bootstrap always disables files and the queue. Callers map application
    settings to this object; changing it does not reconfigure active handlers.
    Call configure_logging() again to apply changes.
    """

    # Default logger name and prefix of the default aggregate log filename.
    app_name: str = "app"
    # Accept TRACE, standard level names or integers; unknown names warn and use INFO.
    level: str | int = "INFO"
    # Select single-line JSON or readable text for both console and files.
    format: LogFormat = "json"
    # Base directory for file logs, with ~ expansion. Required when either file
    # output is enabled, even for an absolute total_file; the manager creates it.
    log_dir: Path | str | None = None

    # Enable console output independently of module and aggregate files.
    enable_console: bool = True
    # True selects stderr, False selects stdout; config diagnostics always use stderr.
    console_to_stderr: bool = True
    # Include structured fields on the console; files always include them.
    console_include_fields: bool = False
    # Include full tracebacks on the console; files always retain available exceptions.
    console_include_exception: bool = False
    # Text only: with file output enabled, console records at ERROR or above show
    # a summary and "details in log file", without fields or tracebacks. JSON is unaffected.
    simplify_console_errors: bool = True

    # Route by logger name, creating each file lazily on its first record.
    enable_module_file: bool = False
    # Module file directory, usually a relative subdirectory of log_dir.
    module_dir_name: str = "modules"
    # Module handler cache limit, at least 1. New paths beyond the limit use
    # overflow.log, adding at most one handler without evicting cached handlers.
    max_module_handlers: int = 128
    # Match logger names with str.startswith(tuple); an empty tuple accepts all.
    # Unmatched names use external.log. Prefixes do not enforce package boundaries.
    module_name_prefixes: tuple[str, ...] = ()

    # Also write all records to one aggregate file, independently of module files.
    enable_total_file: bool = False
    # None selects log_dir/<app_name>.log; relative paths use log_dir, absolute paths stand alone.
    total_file: Path | str | None = None

    # daily rotates on the first write after local midnight; size uses size_limit.
    # timestamp_utc controls record timestamps without changing the rotation timezone.
    rotation: RotationMode = "daily"
    # Backup count per file, not a number of days; minimum 0. With 0, daily mode
    # does not delete old backups, while size mode creates no rotated backups
    # and the active file may keep growing. Zero does not mean delete all logs.
    retention: int = 14
    # Size mode only: integer bytes or B/KB/MB/GB strings using powers of 1024.
    # For example, 100MB = 104857600 bytes; this is a rotation threshold, not a disk quota.
    size_limit: str | int = "100MB"

    # True writes through an in-process listener thread; False writes in the caller.
    # The queue is process-local and does not guarantee delivery after a crash.
    use_queue: bool = True
    # Capacity in records, at least 1; used only with use_queue=True and active sinks.
    queue_capacity: int = 10_000
    # On a full queue, drop discards and counts immediately; block waits up to
    # queue_block_timeout before discarding and counting. sync_fallback writes in
    # the caller, potentially increasing latency and overtaking queued records.
    queue_full_policy: QueueFullPolicy = "drop"
    # Seconds to wait under block, at least 0; not an I/O, handover or shutdown timeout.
    queue_block_timeout: float = 0.1
    # Propagate to parent loggers when root is unmanaged; parent sinks may duplicate output.
    # manage_root_logger=True disables propagation on owned loggers to prevent duplicates.
    propagate: bool = False
    # Capture Python warnings continuously while enabled; restore prior state
    # when disabled or shut down.
    capture_warnings: bool = False
    # Own root to receive third-party records that propagate to it; records stopped
    # upstream are not captured. Restore prior handlers, level and other state on
    # disable or shutdown. Root is left unmanaged by default.
    manage_root_logger: bool = False
    # True emits ISO timestamps with UTC timezone; False uses naive local timestamps.
    timestamp_utc: bool = True

    # Process fallback for contexts without a nonempty trace ID; updated after activation succeeds.
    default_trace_id: str = "system"
    # Optional processor after merging context < bind < call fields; later values win.
    # Called separately by each sink's formatter, in the listener or caller thread;
    # must tolerate repeated calls. Receives a shallow copy. On failure, retain the
    # original fields and add _field_processor_error.
    field_processor: FieldProcessor | None = None
    # JSON encoder, also used for text field suffixes; must return str. Called with
    # ensure_ascii=False first, then retried once without that keyword on TypeError.
    # The default uses str() for non-JSON objects; custom encoders must handle them.
    json_dumps: JsonDumps = _default_json_dumps

    def normalized(self) -> LogConfig:
        """Return a normalized copy without creating directories or activating logging.

        Expand ~ in paths, lowercase format/rotation and enforce numeric minima.
        Unknown format, rotation and queue_full_policy warn and fall back to
        json, size and drop respectively. Nonconvertible numbers still raise
        TypeError/ValueError. Level, size and directory validation is delegated
        to the level parser, file handler and manager respectively.
        """
        log_dir = Path(self.log_dir).expanduser() if self.log_dir is not None else None
        total_file = Path(self.total_file).expanduser() if self.total_file is not None else None
        rotation: RotationMode = "daily" if str(self.rotation).lower() == "daily" else "size"
        fmt: LogFormat = "text" if str(self.format).lower() == "text" else "json"
        queue_full_policy: QueueFullPolicy = (
            self.queue_full_policy
            if self.queue_full_policy in {"drop", "block", "sync_fallback"}
            else "drop"
        )
        normalized = replace(
            self,
            format=fmt,
            log_dir=log_dir,
            total_file=total_file,
            rotation=rotation,
            retention=max(0, int(self.retention)),
            max_module_handlers=max(1, int(self.max_module_handlers)),
            queue_capacity=max(1, int(self.queue_capacity)),
            queue_full_policy=queue_full_policy,
            queue_block_timeout=max(0.0, float(self.queue_block_timeout)),
        )
        for name, valid, allowed, risk in (
            (
                "format",
                str(self.format).lower() in {"json", "text"},
                "json, text",
                "log output may not match the expected parser",
            ),
            (
                "rotation",
                str(self.rotation).lower() in {"daily", "size"},
                "daily, size",
                "rotation may not follow the intended policy",
            ),
            (
                "queue_full_policy",
                self.queue_full_policy in {"drop", "block", "sync_fallback"},
                "drop, block, sync_fallback",
                "records may be discarded when the queue is full",
            ),
            (
                "retention",
                int(self.retention) >= 0,
                "an integer >= 0",
                "disk usage may grow without the intended retention limit",
            ),
            (
                "max_module_handlers",
                int(self.max_module_handlers) >= 1,
                "an integer >= 1",
                "more logger names may be routed to the overflow file",
            ),
            (
                "queue_capacity",
                int(self.queue_capacity) >= 1,
                "an integer >= 1",
                "the queue may fill more often and trigger its full policy",
            ),
            (
                "queue_block_timeout",
                float(self.queue_block_timeout) >= 0,
                "a number >= 0 (seconds)",
                "queue insertion may time out immediately when full",
            ),
        ):
            if not valid:
                warn_config_fallback(
                    name,
                    getattr(self, name),
                    getattr(normalized, name),
                    allowed=allowed,
                    risk=risk,
                )
        return normalized


def config_from_env(
    *,
    app_name: str = "app",
    default_log_dir: Path | str | None = None,
    env_prefix: str = "CELLS_LOG",
    base: LogConfig | None = None,
) -> LogConfig:
    """Return normalized environment overrides on base without activating logging.

    Args:
        app_name: Application name overriding base.app_name unless empty.
        default_log_dir: Directory fallback taking precedence over base.log_dir.
        env_prefix: Variable prefix; generated names are uppercased.
        base: Baseline config; fields without environment overrides are retained.

    Returns:
        A normalized copy with applicable environment overrides.

    Environment names use <PREFIX>_<NAME>. Supported names are LEVEL, FORMAT,
    DIR, FILE, ENABLE_CONSOLE, ENABLE_MODULE_FILE, ENABLE_TOTAL_FILE, ROTATION,
    RETENTION, SIZE_LIMIT, USE_QUEUE, QUEUE_CAPACITY, QUEUE_FULL_POLICY,
    CAPTURE_WARNINGS and MANAGE_ROOT_LOGGER. Set other fields through base or
    LogConfig. Booleans accept 1/true/yes/y/on and 0/false/no/n/off regardless of
    case. Invalid booleans or integers warn and retain baseline values; empty
    DIR/FILE retain path fallbacks. LEVEL is parsed by name, so numeric strings
    are not converted to numeric levels. Pass custom integers via LogConfig.level.
    """
    cfg = base or LogConfig(app_name=app_name, log_dir=default_log_dir)

    log_dir = env_path(
        env_key(env_prefix, "DIR"),
        Path(default_log_dir).expanduser() if default_log_dir else cfg.log_dir,
    )
    total_file = env_path(env_key(env_prefix, "FILE"), cfg.total_file)

    return replace(
        cfg,
        app_name=app_name or cfg.app_name,
        level=os.getenv(env_key(env_prefix, "LEVEL"), str(cfg.level)),
        format=os.getenv(env_key(env_prefix, "FORMAT"), str(cfg.format)).lower(),  # type: ignore[arg-type]
        log_dir=log_dir,
        total_file=total_file,
        enable_console=env_bool(env_key(env_prefix, "ENABLE_CONSOLE"), cfg.enable_console),
        enable_module_file=env_bool(
            env_key(env_prefix, "ENABLE_MODULE_FILE"), cfg.enable_module_file
        ),
        enable_total_file=env_bool(env_key(env_prefix, "ENABLE_TOTAL_FILE"), cfg.enable_total_file),
        rotation=os.getenv(env_key(env_prefix, "ROTATION"), str(cfg.rotation)).lower(),  # type: ignore[arg-type]
        retention=env_int(env_key(env_prefix, "RETENTION"), cfg.retention),
        size_limit=env_size(env_key(env_prefix, "SIZE_LIMIT"), cfg.size_limit),
        use_queue=env_bool(env_key(env_prefix, "USE_QUEUE"), cfg.use_queue),
        queue_capacity=env_int(env_key(env_prefix, "QUEUE_CAPACITY"), cfg.queue_capacity),
        queue_full_policy=os.getenv(
            env_key(env_prefix, "QUEUE_FULL_POLICY"),
            cfg.queue_full_policy,
        ).lower(),  # type: ignore[arg-type]
        capture_warnings=env_bool(env_key(env_prefix, "CAPTURE_WARNINGS"), cfg.capture_warnings),
        manage_root_logger=env_bool(
            env_key(env_prefix, "MANAGE_ROOT_LOGGER"),
            cfg.manage_root_logger,
        ),
    ).normalized()
