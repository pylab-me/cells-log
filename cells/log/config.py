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

    Defaults apply to explicit production configuration: console JSON with
    synchronous delivery, no files, no root ownership and no warnings capture.
    Bootstrap always disables files and the queue. Callers map application
    settings to this object; changing it does not reconfigure active handlers.
    Call configure_logging() again to apply changes.

    Fields merge in context < stdlib extra < bind < call order; later values win. Field hooks
    receive shallow copies once per record on first sink formatting,
    in the listener or caller thread. Failures discard all original fields. JSON hooks must return str and handle
    non-JSON objects. They receive ensure_ascii=False, with one retry without
    that keyword on TypeError; they also encode text output's field suffixes.
    """

    # Options: <str>. "app" (default) is the fallback logger name and aggregate filename prefix.
    app_name: str = "app"
    # Options: <"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "NOTSET", int>.
    # "INFO" (default); WARN/FATAL aliases accepted. Unknown names warn and use INFO.
    level: str | int = "INFO"
    # Options: <"json", "text">. "json" (default) for single-line JSON;
    # "text" for human-readable output. Applies to console and files.
    format: LogFormat = "json"
    # Options: <Path, str, None>. None (default); required for any file output, even
    # with an absolute total_file. Expands ~; the manager creates the directory.
    log_dir: Path | str | None = None

    # Options: <True, False>. True (default) enables console output; False disables it.
    # Independent of module and aggregate file output.
    enable_console: bool = True
    # Options: <True, False>. True (default) writes to stderr; False writes to stdout.
    # Configuration diagnostics always use stderr.
    console_to_stderr: bool = True
    # Options: <True, False>. True (default) includes console fields.
    # Files always include fields; simplified text errors omit them on the console.
    console_include_fields: bool = True
    # Options: <True, False>. True (default) includes console tracebacks.
    # Files retain available exceptions; simplified text errors omit console tracebacks.
    console_include_exception: bool = True
    # Options: <True, False>. True (default) shows only a summary and "details in log file"
    # for console text errors >= ERROR when files are enabled; False keeps normal detail.
    simplify_console_errors: bool = True

    # Options: <True, False>. False (default) disables module files; True routes by logger name.
    # Each file is created lazily on its first record; bootstrap always disables this option.
    enable_module_file: bool = False
    # Options: <str>. "modules" (default) is the module file subdirectory under log_dir.
    # An absolute path selects a directory independently of log_dir.
    module_dir_name: str = "modules"
    # Options: <int >= 1>. 128 (default) cached module handlers; new paths beyond the limit
    # use overflow.log, adding at most one handler without evicting existing handlers.
    max_module_handlers: int = 128
    # Options: <tuple[str, ...]>. () (default) accepts all names; otherwise str.startswith
    # matches prefixes, without package boundaries. Unmatched names use external.log.
    module_name_prefixes: tuple[str, ...] = ()

    # Options: <True, False>. False (default) disables the aggregate file; True writes all records.
    # Independent of module files; bootstrap always disables this option.
    enable_total_file: bool = False
    # Options: <Path, str, None>. None (default) selects log_dir/<app_name>.log;
    # relative paths use log_dir, absolute paths stand alone. Expands ~.
    total_file: Path | str | None = None

    # Options: <"daily", "size">. "daily" (default) rotates on the first write after local
    # midnight; "size" uses size_limit. timestamp_utc does not change the rotation timezone.
    rotation: RotationMode = "daily"
    # Options: <int >= 0>. 14 (default) backups per file, not days. With 0, daily keeps all
    # backups; size creates none and the active file may keep growing. Zero does not delete logs.
    retention: int = 14
    # Options: <int bytes, str bytes/B/KB/MB/GB>. "100MB" (default) = 104857600 bytes.
    # Size rotation only; units use powers of 1024. A per-file threshold, not a total disk quota.
    size_limit: str | int = "100MB"

    # Options: <True, False>. False (default) writes synchronously; True uses a local listener.
    # No crash delivery guarantee; bootstrap always disables the queue.
    use_queue: bool = False
    # Options: <int >= 1>. 10_000 (default) queued records, excluding in-flight writes.
    # Applies only with use_queue=True and active output sinks.
    queue_capacity: int = 10_000
    # Options: <"drop", "block", "sync_fallback">. "drop" (default) discards and counts on full;
    # "block" waits or drops on timeout; "sync_fallback" writes in caller, risking delay/reordering.
    queue_full_policy: QueueFullPolicy = "drop"
    # Options: <float >= 0>. 0.1 (default) seconds to wait for space under "block", then drop/count.
    # Zero does not wait; this is not a file I/O, handover or shutdown timeout.
    queue_block_timeout: float = 0.1
    # Options: <str/int level, None>. None (default) keeps the chosen overflow
    # policy for all records. When set, records at this level or above use
    # synchronous fallback after a full queue (or a block timeout).
    # May block the producer and overtake queued records.
    queue_fallback_min_level: str | int | None = None
    # Options: <True, False>. False (default) stops propagation; True also uses parent loggers,
    # potentially duplicating output. manage_root_logger=True forces False on owned loggers.
    propagate: bool = False
    # Options: <True, False>. False (default) leaves warnings unmanaged; True captures them as logs.
    # Capture stays active across reconfiguration; disabling or shutdown restores prior state.
    capture_warnings: bool = False
    # Options: <True, False>. False (default) leaves root unmanaged; True captures records at root.
    # Records stopped upstream are excluded; disabling or shutdown restores prior handlers/state.
    manage_root_logger: bool = False
    # Options: <True, False>. True (default) emits ISO timestamps with UTC timezone;
    # False emits local ISO timestamps without a timezone offset.
    timestamp_utc: bool = True

    # Options: <str>. "system" (default) is the fallback for an empty or unset context trace ID.
    # Updated only after configuration activation succeeds.
    default_trace_id: str = "system"
    # Options: <None, callable>. None (default) keeps merged fields; callable returns new fields.
    # On failure, discard ALL fields and add only _field_processor_error (exception type).
    field_processor: FieldProcessor | None = None
    # Options: <callable>. _default_json_dumps (default) uses ensure_ascii=False and default=str.
    # Custom encoders must return str; see hook rules above for invocation and retry behavior.
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
    QUEUE_FALLBACK_MIN_LEVEL,
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
        queue_fallback_min_level=(
            os.getenv(env_key(env_prefix, "QUEUE_FALLBACK_MIN_LEVEL"))
            or cfg.queue_fallback_min_level
        ),
        capture_warnings=env_bool(env_key(env_prefix, "CAPTURE_WARNINGS"), cfg.capture_warnings),
        manage_root_logger=env_bool(
            env_key(env_prefix, "MANAGE_ROOT_LOGGER"),
            cfg.manage_root_logger,
        ),
    ).normalized()
