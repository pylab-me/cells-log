# cells-log

`cells-log` is a lightweight structured logging kernel for Python services, CLIs, async workers and data pipelines.

It keeps Python's stdlib `logging` ecosystem underneath, but exposes a small bound-logger API with bootstrap-safe initialization, structured fields, `ContextVar`-based trace context, file routing and optional queue-based writing.

The import package path is `cells.log`.

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

from cells.log import (
    LogConfig,
    configure_logging,
    get_logger,
    reset_trace_id,
    set_trace_id,
    shutdown_logging,
)


configure_logging(
    LogConfig(
        app_name="app",
        level="INFO",
        format="json",
        log_dir=Path("./logs"),
        enable_console=True,
        enable_module_file=True,
        enable_total_file=True,
        total_file="app.log",
        module_name_prefixes=("app",),
    )
)

trace_token = set_trace_id("trace-001")
try:
    log = get_logger("app.worker").bind(stage="worker")
    log.info("task completed", task_id="t1", duration_ms=12.3)
finally:
    reset_trace_id(trace_token)
    shutdown_logging()
```

Example JSON line:

```json
{"timestamp":"2026-05-11T00:00:00+00:00","level":"INFO","logger":"app.worker","message":"task completed","trace_id":"trace-001","path":"/app/worker.py:42","func":"run","fields":{"channel":"app.worker","log_tag":"app_worker","stage":"worker","task_id":"t1","duration_ms":12.3}}
```

## Public API

```python
from cells.log import (
    LogConfig,
    bootstrap_logging,
    configure_logging,
    configure_logging_from_env,
    get_logger,
    get_queue_stats,
    set_trace_id,
    reset_trace_id,
    bind_log_context,
    reset_log_context,
    shutdown_logging,
)
```

### `bootstrap_logging()`

Initializes console-only logging from environment variables. This is safe before application settings are loaded. File sinks and queue writing stay disabled during bootstrap, even when their environment flags are enabled.

```python
from cells.log import bootstrap_logging, get_logger


bootstrap_logging(app_name="demo")
get_logger("demo.boot").info("bootstrap started")
```

### `configure_logging()`

Configures the production handler graph from an explicit `LogConfig`.

```python
from cells.log import LogConfig, configure_logging


configure_logging(LogConfig(app_name="demo", level="DEBUG", format="text"))
```

Reconfiguration prepares the replacement while the current configuration continues serving logs. Preparation failure leaves the current configuration active. Handover briefly gates delivery to drain accepted queue records and transfer file streams; activation failure restores the previous configuration. After a successful switch, old resources are cleaned up while the replacement serves logs.

Use `force=False` to keep an existing production configuration. Call `shutdown_logging()` before process exit to drain queued records and close managed handlers.

### `configure_logging_from_env()`

Builds `LogConfig` from environment variables.

```python
from pathlib import Path
from cells.log import configure_logging_from_env


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
| `CELLS_LOG_QUEUE_CAPACITY` | Maximum number of buffered log records |
| `CELLS_LOG_QUEUE_FULL_POLICY` | `drop`, `block`, or `sync_fallback` |
| `CELLS_LOG_CAPTURE_WARNINGS` | Route Python warnings into logging |
| `CELLS_LOG_MANAGE_ROOT_LOGGER` | Route root logger records through cells-log |

## Configuration model

`LogConfig` controls console and file sinks, rotation, queue backpressure, root/warnings capture, the fallback trace ID and serialization hooks. See [`cells/log/config.py`](cells/log/config.py) for all fields and defaults.

See [`docs/architecture.md`](docs/architecture.md) for the full design boundary.

### Queue backpressure

In **v0.3.0** the default is **synchronous delivery** (`use_queue=False`): for fast stdout or local file sinks a background listener may add CPU overhead instead of improving throughput. Opt into `use_queue=True` when application threads must avoid a **slow** file/network sink and can tolerate buffered delivery. The in-process queue is bounded (default capacity: 10,000 records). Choose `queue_full_policy` according to latency and loss requirements:

| Policy | Behavior when the queue is full |
|---|---|
| `drop` (default) | Discard the record and increment `dropped_records` |
| `block` | Wait up to `queue_block_timeout` seconds, then discard and count the record |
| `sync_fallback` | Write the record synchronously on the calling thread |

`get_queue_stats()` returns `enabled`, `capacity`, `depth`, `dropped_records`, `accepted_records`, `delivered_records`, `fallback_records`, `delivery_errors`, `max_depth_sampled`, `mean_delivery_latency_ms`, and `max_delivery_latency_ms` for the active queue. The queue counters reset when the queue is replaced. `delivered_records` means listener dispatch completed without a *detected* built-in sink error, **not** that bytes reached durable storage. `delivery_errors` includes detected failures swallowed by built-in console/rotating-file handlers or the module-file router, as well as exceptions escaping sink dispatch; custom handlers that suppress errors remain invisible. Successful writes to one sink cannot cancel a detected failure in another. Queue metrics do not cover synchronous delivery. `max_depth_sampled` checks one event in 32, not the exact queue peak. `sync_fallback` delivery is reported separately and may reorder events.

**Workload selection:** sync mode is the default for CLI, standard container stdout, and ordinary local files. Queue mode is opt-in for potentially blocking sinks and for latency-sensitive producer threads; benchmark both modes on the target deployment. The included benchmark tool compares sync/queue with discard, rotating file, and dual sinks: `python benchmarks/logging_benchmark.py --count 10000 --repeats 5`. Full results and profiling commands are in [`benchmarks/README.md`](benchmarks/README.md).

## Context propagation

`trace_id` and log context are stored in `ContextVar`, so they work naturally in async pipelines and request-scoped code.

```python
from cells.log import bind_log_context, reset_log_context, set_trace_id, reset_trace_id


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
from cells.log import LogConfig, configure_logging


def field_processor(fields: dict) -> dict:
    fields = dict(fields)
    if "authorization" in fields:
        fields["authorization"] = "***"
    return fields


configure_logging(LogConfig(field_processor=field_processor))
```

**Security:** Field processors receive a shallow copy of merged business fields and run at most once per record, even with multiple sinks. If the processor raises or returns an invalid mapping, the complete input field dictionary is discarded and only a sanitized `_field_processor_error` type is emitted. Do not embed secrets in the free-form message or exception text: those are **not** processed by this hook.

Standard `logging.getLogger("library").warning("event", extra={"duration_ms": 123})` fields are emitted as JSON fields when `manage_root_logger=True`, without accidentally emitting reserved `LogRecord` internals.

Custom JSON serializer:

```python
import json
from cells.log import LogConfig, configure_logging


configure_logging(LogConfig(json_dumps=json.dumps))
```

## Fork and multi-worker deployment

When a parent has already initialized logging and forks, cells-log detaches inherited queue listeners and sinks in the child. The child intentionally **does not log through the inherited graph**; configure an independent `LogConfig` inside each worker before emitting logs. This avoids waiting forever on a parent-only listener. If `manage_root_logger` is enabled, the child should likewise configure it for its own worker lifecycle.

**Do not point multiple processes at the same rotating log file.** Standard rotating handlers are process-local. For Gunicorn/multiprocessing-style deployments, prefer per-worker JSON stdout collected by the deployment runtime or give each worker distinct file paths. Fork reinitialization does not turn local files into multi-process-safe sinks.

Logger names acquired through `get_logger()` temporarily replace their stdlib handlers/level; original states are restored by `shutdown_logging()`. Root/warnings capture remains opt-in.

## File layout

With module and total logs enabled:

```text
logs/
  app.log
  modules/
    app.log
    app_worker.log
    external.log
    overflow.log
```

Routing notes:

- `module_name_prefixes=("app",)` keeps application logs in their own module files.
- Loggers outside the allowed prefix are routed into `external.log`.
- If too many dynamic logger names are created, excess records are routed into `overflow.log` instead of opening unlimited file descriptors.
- Names that map to the same file share a rotating handler, including case variants on Windows; each record retains its original logger name. v0.3.1 caches up to `max_module_handlers` hot names to avoid repeated filename/path calculations; arbitrary dynamic names cannot grow that cache without bound.

## GitHub Actions

This repository includes two workflows:

- `.github/workflows/build.yml`: Python 3.11–3.14 tests on Linux and Windows, wheel artifact build, and GitHub Release publishing.
- `.github/workflows/publish-pypi.yml`: release/manual wheel build, metadata check and PyPI publish.

After tests and wheel metadata checks pass, `build.yml` publishes the built wheel according to the triggering ref:

| Trigger | GitHub Release |
|---|---|
| Push to `main` | Update the `rolling` tag to the tested commit and publish a prerelease titled `rolling` |
| Push a `v*` tag | Publish a release using the tag as its title |
| Other pushes, pull requests, manual builds | Build and upload workflow artifacts only |

The rolling release replaces matching wheel assets and removes wheel assets from older versions. It is not marked as the latest stable release. Publishing uses the built-in `GITHUB_TOKEN` with `contents: write` permission limited to the release job.

Releases created with `GITHUB_TOKEN` do not trigger the separate PyPI workflow; use its manual trigger for those builds. See [GitHub's workflow triggering documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

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
