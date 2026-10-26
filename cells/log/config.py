"""Configuration model for cells-log."""

from __future__ import annotations

import json as std_json
import os
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from cells.log.env import (
    env_bool,
    env_console_color,
    env_int,
    env_key,
    env_path,
    env_size,
    warn_config_fallback,
)

LogFormat = Literal["json", "text"]
ConsoleColor = Literal[False, True, "auto", "always"]
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
    # "text" for human-readable output. Default inherited by console and file
    # unless format_for_console or format_for_file overrides it.
    format: LogFormat = "json"
    # None inherits format. These names express what format is used for each sink.
    format_for_console: LogFormat | None = None
    format_for_file: LogFormat | None = None
    # Options: <Path, str, None>. None (default); required for any file output, even
    # with an absolute total_file. Expands ~; the manager creates the directory.
    log_dir: Path | str | None = None

    # Options: <True, False>. True (default) enables console output; False disables it.
    # Independent of module and aggregate file output.
    enable_console: bool = True
    # Options: <True, False>. True (default) writes to stderr; False writes to stdout.
    # Configuration diagnostics always use stderr.
    console_to_stderr: bool = True
    # Options: False (default), True/"auto", "always". False disables ANSI entirely.
    # True/"auto" colors text only when the selected output stream is a TTY;
    # "always" forces ANSI even through Docker pipes. JSON and files stay plain.
    console_color: ConsoleColor = False
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
    # Exact names only. Temporarily replace named logger handlers and restore at shutdown.
    # Do not list "root" or "py.warnings" (the latter uses capture_warnings).
    capture_loggers: tuple[str, ...] | list[str] = ()
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
        self._validate_capture_names()
        log_dir = Path(self.log_dir).expanduser() if self.log_dir is not None else None
        total_file = Path(self.total_file).expanduser() if self.total_file is not None else None
        rotation: RotationMode = "daily" if str(self.rotation).lower() == "daily" else "size"
        fmt: LogFormat = "text" if str(self.format).lower() == "text" else "json"
        format_for_console = (
            None if self.format_for_console is None else str(self.format_for_console).lower()
        )
        format_for_file = (
            None if self.format_for_file is None else str(self.format_for_file).lower()
        )
        color = self.console_color
        console_color: ConsoleColor = (
            color if color is False or color is True or color in ("auto", "always") else False
        )
        queue_full_policy: QueueFullPolicy = (
            self.queue_full_policy
            if self.queue_full_policy in {"drop", "block", "sync_fallback"}
            else "drop"
        )
        normalized = replace(
            self,
            format=fmt,
            console_color=console_color,
            capture_loggers=tuple(self.capture_loggers),
            format_for_console=(
                "text"
                if format_for_console == "text"
                else "json"
                if format_for_console is not None
                else None
            ),
            format_for_file=(
                "text"
                if format_for_file == "text"
                else "json"
                if format_for_file is not None
                else None
            ),
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
                "console_color",
                self.console_color is False
                or self.console_color is True
                or self.console_color in ("auto", "always"),
                "False, True, auto, always",
                "ANSI escapes may be emitted to an unintended consumer",
            ),
            (
                "format",
                str(self.format).lower() in {"json", "text"},
                "json, text",
                "log output may not match the expected parser",
            ),
            (
                "format_for_console",
                format_for_console in {None, "json", "text"},
                "json, text, or None (inherit)",
                "the console output may not match the expected parser",
            ),
            (
                "format_for_file",
                format_for_file in {None, "json", "text"},
                "json, text, or None (inherit)",
                "the file output may not match the expected parser",
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
        normalized._validate_capture_names()
        return normalized

    def _validate_capture_names(self) -> None:
        """Reject ambiguous and unsafe external logger ownership."""
        if not isinstance(self.capture_loggers, (tuple, list)) or any(
            not isinstance(name, str) or not name.strip() or name != name.strip()
            for name in self.capture_loggers
        ):
            raise ValueError("capture_loggers must be a sequence of nonempty logger names")
        if len(set(self.capture_loggers)) != len(self.capture_loggers):
            raise ValueError("capture_loggers must not contain duplicates")
        if "root" in self.capture_loggers or "py.warnings" in self.capture_loggers:
            raise ValueError("use manage_root_logger/capture_warnings for special loggers")

    def validate(self) -> None:
        """Validate configuration strictly without modifying it or emitting fallbacks.

        Use before configure_logging(..., strict=True) when configuration
        mistakes must raise rather than be normalized to possibly unsafe defaults.
        """
        for name in ("format", "format_for_console", "format_for_file"):
            raw = getattr(self, name)
            if (raw is None and name == "format") or (
                raw is not None and str(raw).lower() not in {"json", "text"}
            ):
                raise ValueError(f"{name} must be 'json' or 'text'")
        if not (
            self.console_color is False
            or self.console_color is True
            or self.console_color in ("auto", "always")
        ):
            raise ValueError("console_color must be False, True, 'auto' or 'always'")
        if str(self.rotation).lower() not in {"daily", "size"}:
            raise ValueError("rotation must be 'daily' or 'size'")
        if self.queue_full_policy not in {"drop", "block", "sync_fallback"}:
            raise ValueError("queue_full_policy must be drop, block or sync_fallback")
        for name, lower in (("retention", 0), ("max_module_handlers", 1), ("queue_capacity", 1)):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < lower:
                raise ValueError(f"{name} must be an integer >= {lower}")
        if (
            isinstance(self.queue_block_timeout, bool)
            or not isinstance(self.queue_block_timeout, (float, int))
            or not 0 <= self.queue_block_timeout < float("inf")
        ):
            raise ValueError("queue_block_timeout must be finite and nonnegative")
        for name in ("level", "queue_fallback_min_level"):
            level = getattr(self, name)
            if level is None:
                if name == "level":
                    raise ValueError("level must be a logging level")
                continue
            if isinstance(level, int) and not isinstance(level, bool):
                continue
            if not isinstance(level, str) or (
                not isinstance(getattr(logging, level.strip().upper(), None), int)
                and level.strip().upper() != "TRACE"
            ):
                raise ValueError(f"{name} is not a supported logging level: {level!r}")
        if isinstance(self.size_limit, bool) or not (
            isinstance(self.size_limit, int)
            and self.size_limit >= 0
            or isinstance(self.size_limit, str)
            and re.fullmatch(r"\s*\d+\s*(?:B|KB|MB|GB)?\s*", self.size_limit, re.I) is not None
        ):
            raise ValueError("size_limit must be a nonnegative byte count or B/KB/MB/GB")
        if (self.enable_module_file or self.enable_total_file) and self.log_dir is None:
            raise ValueError("log_dir is required for file logging")
        self._validate_capture_names()


def config_from_env(
    *,
    app_name: str | None = None,
    default_log_dir: Path | str | None = None,
    env_prefix: str = "CELLS_LOG",
    base: LogConfig | None = None,
) -> LogConfig:
    """Return normalized environment overrides on base without activating logging.

    Args:
        app_name: Explicit application name; omit to preserve base.app_name.
        default_log_dir: Directory fallback taking precedence over base.log_dir.
        env_prefix: Variable prefix; generated names are uppercased.
        base: Baseline config; fields without environment overrides are retained.

    Returns:
        A normalized copy with applicable environment overrides.

    Environment names use <PREFIX>_<NAME>. Supported names are LEVEL, FORMAT,
    FORMAT_FOR_CONSOLE, FORMAT_FOR_FILE, CAPTURE_LOGGERS,
    DIR, FILE, ENABLE_CONSOLE, CONSOLE_COLOR, ENABLE_MODULE_FILE, ENABLE_TOTAL_FILE, ROTATION,
    RETENTION, SIZE_LIMIT, USE_QUEUE, QUEUE_CAPACITY, QUEUE_FULL_POLICY,
    QUEUE_FALLBACK_MIN_LEVEL,
    CAPTURE_WARNINGS and MANAGE_ROOT_LOGGER. Set other fields through base or
    LogConfig. Booleans accept 1/true/yes/y/on and 0/false/no/n/off regardless of
    case. Invalid booleans or integers warn and retain baseline values; empty
    DIR/FILE retain path fallbacks. LEVEL is parsed by name, so numeric strings
    are not converted to numeric levels. Pass custom integers via LogConfig.level.
    """
    cfg = (
        base if base is not None else LogConfig(app_name=app_name or "app", log_dir=default_log_dir)
    )

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
        format_for_console=(
            os.getenv(env_key(env_prefix, "FORMAT_FOR_CONSOLE"), cfg.format_for_console) or None
        ),  # type: ignore[arg-type]
        format_for_file=(
            os.getenv(env_key(env_prefix, "FORMAT_FOR_FILE"), cfg.format_for_file) or None
        ),  # type: ignore[arg-type]
        log_dir=log_dir,
        total_file=total_file,
        enable_console=env_bool(env_key(env_prefix, "ENABLE_CONSOLE"), cfg.enable_console),
        console_color=env_console_color(env_key(env_prefix, "CONSOLE_COLOR"), cfg.console_color),
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
        capture_loggers=(
            tuple(
                name.strip()
                for name in os.environ[env_key(env_prefix, "CAPTURE_LOGGERS")].split(",")
            )
            if os.environ.get(env_key(env_prefix, "CAPTURE_LOGGERS"), "").strip()
            else cfg.capture_loggers
        ),
    ).normalized()
