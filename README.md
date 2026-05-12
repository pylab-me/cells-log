# cells-log

`cells-log` is a lightweight structured logging kernel for Python services, CLIs, async workers and data pipelines.

It keeps Python's stdlib `logging` ecosystem underneath, but exposes a small bound-logger API with bootstrap-safe initialization, structured fields, `ContextVar`-based trace context, file routing and optional queue-based writing.

The import package name is `cells_log`.

## Why this package exists

Most projects start with `logging.basicConfig()` and eventually grow a mixed set of console logs, JSON logs, module files, request IDs and ad-hoc wrappers. `cells-log` is designed to keep that layer small and stable:

- Application code uses one bound logger API.
- Infrastructure code controls sinks, formatters and lifecycle.
- Project-specific policy, such as field serialization or redaction, is injected through hooks instead of hard-coded into the kernel.
- Bootstrap logs work before full application settings are available.

## Installation

From source:

```bash
python -m pip install -e .
```

From a built wheel:

```bash
python -m pip install dist/cells_log-*.whl
```

## Quick start

```python
from pathlib import Path

from cells_log import LogConfig, configure_logging, get_logger, set_trace_id

configure_logging(
    LogConfig(
        app_name="pokaemon",
        level="INFO",
        format="json",
        log_dir=Path("./logs"),
        enable_console=True,
        enable_module_file=True,
        enable_total_file=True,
        total_file="pokaemon.log",
        module_name_prefixes=("pokaemon",),
    )
)

set_trace_id("trace-001")
log = get_logger("pokaemon.collector").bind(stage="collector")
log.info("task completed", task_id="t1", duration_ms=12.3)
```

Example JSON line:

```json
{"timestamp":"2026-05-11T00:00:00+00:00","level":"INFO","logger":"pokaemon.collector","message":"task completed","trace_id":"trace-001","path":"/app/collector.py:42","func":"run","fields":{"channel":"pokaemon.collector","log_tag":"pokaemon_collector","stage":"collector","task_id":"t1","duration_ms":12.3}}
```

## Public API

```python
from cells_log import (
    LogConfig,
    bootstrap_logging,
    configure_logging,
    configure_logging_from_env,
    get_logger,
    set_trace_id,
    reset_trace_id,
    bind_log_context,
    reset_log_context,
    shutdown_logging,
)
```

### `bootstrap_logging()`

Initializes console-only logging from environment variables. This is safe before application settings are loaded.

```python
from cells_log import bootstrap_logging, get_logger

bootstrap_logging(app_name="demo")
get_logger("demo.boot").info("bootstrap started")
```

### `configure_logging()`

Configures the production handler graph from an explicit `LogConfig`.

```python
from cells_log import LogConfig, configure_logging

configure_logging(LogConfig(app_name="demo", level="DEBUG", format="text"))
```

### `configure_logging_from_env()`

Builds `LogConfig` from environment variables.

```python
from pathlib import Path
from cells_log import configure_logging_from_env

configure_logging_from_env(
    app_name="demo",
    default_log_dir=Path("./logs"),
    env_prefix="CELLS_LOG",
)
```

## Environment variables

When using `configure_logging_from_env(env_prefix="CELLS_LOG")`, supported variables include:

| Variable | Meaning |
|---|---|
| `CELLS_LOG_LEVEL` | `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `CELLS_LOG_FORMAT` | `json` or `text` |
| `CELLS_LOG_DIR` | Log directory used by file sinks |
| `CELLS_LOG_FILE` | Total log file path or file name |
| `CELLS_LOG_ENABLE_CONSOLE` | Enable console sink |
| `CELLS_LOG_ENABLE_MODULE_FILE` | Enable per-module file sink |
| `CELLS_LOG_ENABLE_TOTAL_FILE` | Enable total file sink |
| `CELLS_LOG_ROTATION` | `daily` or `size` |
| `CELLS_LOG_RETENTION` | Number of rotated files to keep |
| `CELLS_LOG_SIZE_LIMIT` | Size rotation limit, for example `100MB` |
| `CELLS_LOG_USE_QUEUE` | Enable in-process `QueueHandler` / `QueueListener` |
| `CELLS_LOG_CAPTURE_WARNINGS` | Route Python warnings into logging |

## Configuration model

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

@dataclass(slots=True)
class LogConfig:
    app_name: str = "app"
    level: str | int = "INFO"
    format: Literal["json", "text"] = "json"
    log_dir: Path | str | None = None

    enable_console: bool = True
    enable_module_file: bool = False
    enable_total_file: bool = False

    rotation: Literal["daily", "size"] = "daily"
    retention: int = 14
    size_limit: str | int = "100MB"

    use_queue: bool = True
    field_processor: callable | None = None
    json_dumps: callable = ...
```

See [`docs/architecture.md`](docs/architecture.md) for the full design boundary.

## Context propagation

`trace_id` and log context are stored in `ContextVar`, so they work naturally in async pipelines and request-scoped code.

```python
from cells_log import bind_log_context, reset_log_context, set_trace_id, reset_trace_id

trace_token = set_trace_id("request-001")
ctx_token = bind_log_context(request_id="request-001", user_id="u1")

try:
    ...
finally:
    reset_log_context(ctx_token)
    reset_trace_id(trace_token)
```

## Hook model

`cells-log` intentionally does not hard-code business policy. Field serialization, redaction, pruning and normalization should be attached through hooks.

```python
from cells_log import LogConfig, configure_logging


def field_processor(fields: dict) -> dict:
    fields = dict(fields)
    if "authorization" in fields:
        fields["authorization"] = "***"
    return fields


configure_logging(LogConfig(field_processor=field_processor))
```

Custom JSON serializer:

```python
from cells import json
from cells_log import LogConfig, configure_logging

configure_logging(LogConfig(json_dumps=json.dumps))
```

## File layout

With module and total logs enabled:

```text
logs/
  app.log
  modules/
    app.log
    app_worker.log
    _external.log
    _overflow.log
```

Routing notes:

- `module_name_prefixes=("app",)` keeps application logs in their own module files.
- Loggers outside the allowed prefix are routed into `_external.log`.
- If too many dynamic logger names are created, excess records are routed into `_overflow.log` instead of opening unlimited file descriptors.

## GitHub Actions

This repository includes two workflows:

- `.github/workflows/build.yml`: cross-platform tests and wheel artifact build.
- `.github/workflows/publish-pypi.yml`: release/manual wheel build, metadata check and PyPI publish.

Expected repository secret:

```text
PYPI_API_TOKEN
```

Optional R2/S3 artifact variables used by `build.yml` on `v*` tags:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
S3_BUCKET
S3_ENDPOINT
S3_PREFIX
S3_REGION
```

## Pokaemon migration

Use `integrations/pokaemon/log.py` as the replacement adapter for the existing Pokaemon logging module. It preserves these functions:

- `configure_basic_logging()`
- `configure_full_logging(settings_obj)`
- `get_logger(name="pokaemon")`
- `set_trace_id(trace_id)`
- `reset_trace_id(token)`

The adapter maps Pokaemon settings and `POKAEMON_*` environment variables into `cells_log.LogConfig`.

## Development

```bash
python -m pip install -e . pytest
python -m pytest -v
```

Build wheel locally:

```bash
python -m pip install build twine
python -m build --wheel
python -m twine check dist/*.whl
```

## Design status

`cells-log` is intentionally small. The current package aims to cover the common 90% production logging cases without becoming a full observability platform. For OpenTelemetry, sampling, rate limiting, or redaction, prefer integration modules and hooks over changing the core logger API.
