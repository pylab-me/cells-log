"""Configuration model for cells-log."""

from __future__ import annotations

import json as std_json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from cells.log.env import env_bool, env_int, env_key, env_path, env_size


LogFormat = Literal["json", "text"]
RotationMode = Literal["daily", "size"]
QueueFullPolicy = Literal["drop", "block", "sync_fallback"]
FieldProcessor = Callable[[dict[str, Any]], Mapping[str, Any] | dict[str, Any]]
JsonDumps = Callable[..., str]


def _default_json_dumps(obj: Any, **kwargs: Any) -> str:
    """Default JSON dumper with safe fallback for non-primitive objects."""
    kwargs.setdefault("ensure_ascii", False)
    kwargs.setdefault("default", str)
    return std_json.dumps(obj, **kwargs)


@dataclass(slots=True)
class LogConfig:
    """Runtime logging configuration.

    The config intentionally contains only infrastructure options. Application
    settings should be mapped into this object by an adapter layer.
    """

    app_name: str = "app"
    level: str | int = "INFO"
    format: LogFormat = "json"
    log_dir: Path | str | None = None

    enable_console: bool = True
    console_to_stderr: bool = True
    console_include_fields: bool = False
    console_include_exception: bool = False
    simplify_console_errors: bool = True

    enable_module_file: bool = False
    module_dir_name: str = "modules"
    max_module_handlers: int = 128
    module_name_prefixes: tuple[str, ...] = ()

    enable_total_file: bool = False
    total_file: Path | str | None = None

    rotation: RotationMode = "daily"
    retention: int = 14
    size_limit: str | int = "100MB"

    use_queue: bool = True
    queue_capacity: int = 10_000
    queue_full_policy: QueueFullPolicy = "drop"
    queue_block_timeout: float = 0.1
    propagate: bool = False
    capture_warnings: bool = False
    manage_root_logger: bool = False
    timestamp_utc: bool = True

    default_trace_id: str = "system"
    field_processor: FieldProcessor | None = None
    json_dumps: JsonDumps = _default_json_dumps

    def normalized(self) -> LogConfig:
        log_dir = Path(self.log_dir).expanduser() if self.log_dir is not None else None
        total_file = Path(self.total_file).expanduser() if self.total_file is not None else None
        rotation: RotationMode = "daily" if str(self.rotation).lower() == "daily" else "size"
        fmt: LogFormat = "text" if str(self.format).lower() == "text" else "json"
        queue_full_policy: QueueFullPolicy = (
            self.queue_full_policy
            if self.queue_full_policy in {"drop", "block", "sync_fallback"}
            else "drop"
        )
        return replace(
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


def config_from_env(
    *,
    app_name: str = "app",
    default_log_dir: Path | str | None = None,
    env_prefix: str = "CELLS_LOG",
    base: LogConfig | None = None,
) -> LogConfig:
    """Build LogConfig from environment variables.

    Supported variables for prefix CELLS_LOG:
    CELLS_LOG_LEVEL, CELLS_LOG_FORMAT, CELLS_LOG_DIR, CELLS_LOG_FILE,
    CELLS_LOG_ENABLE_CONSOLE, CELLS_LOG_ENABLE_MODULE_FILE,
    CELLS_LOG_ENABLE_TOTAL_FILE, CELLS_LOG_ROTATION, CELLS_LOG_RETENTION,
    CELLS_LOG_SIZE_LIMIT, CELLS_LOG_USE_QUEUE, CELLS_LOG_QUEUE_CAPACITY,
    CELLS_LOG_QUEUE_FULL_POLICY.
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
