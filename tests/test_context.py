from __future__ import annotations

import asyncio
import json
from pathlib import Path
from threading import Thread

from cells.log import (
    LogConfig,
    bind_log_context,
    configure_logging,
    get_log_context,
    get_logger,
    get_trace_id,
    reset_log_context,
    reset_trace_id,
    set_trace_id,
    shutdown_logging,
)


def _read_json_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_default_trace_follows_reconfiguration(tmp_path: Path) -> None:
    configure_logging(
        LogConfig(
            default_trace_id="first",
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            total_file="first.log",
            use_queue=False,
        )
    )
    get_logger("demo").info("first")

    configure_logging(
        LogConfig(
            default_trace_id="second",
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            total_file="second.log",
            use_queue=False,
        )
    )
    get_logger("demo").info("second")
    shutdown_logging()

    assert _read_json_lines(tmp_path / "first.log")[0]["trace_id"] == "first"
    assert _read_json_lines(tmp_path / "second.log")[0]["trace_id"] == "second"


def test_new_thread_uses_configured_default_trace(tmp_path: Path) -> None:
    configure_logging(
        LogConfig(
            default_trace_id="thread-default",
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            use_queue=False,
        )
    )

    thread = Thread(target=lambda: get_logger("worker").info("thread"))
    thread.start()
    thread.join()
    shutdown_logging()

    assert _read_json_lines(tmp_path / "app.log")[0]["trace_id"] == "thread-default"


def test_explicit_trace_reset_returns_to_latest_default() -> None:
    configure_logging(
        LogConfig(
            default_trace_id="configured",
            enable_console=False,
            use_queue=False,
        )
    )

    token = set_trace_id("explicit")
    assert get_trace_id() == "explicit"
    reset_trace_id(token)
    assert get_trace_id() == "configured"


def test_bound_context_is_isolated_between_async_tasks() -> None:
    async def read_context(value: str) -> tuple[str, dict[str, object]]:
        trace_token = set_trace_id(value)
        context_token = bind_log_context(task=value)
        await asyncio.sleep(0)
        result = (get_trace_id(), get_log_context())
        reset_log_context(context_token)
        reset_trace_id(trace_token)
        return result

    async def run_tasks() -> list[tuple[str, dict[str, object]]]:
        return list(await asyncio.gather(read_context("a"), read_context("b")))

    assert asyncio.run(run_tasks()) == [
        ("a", {"task": "a"}),
        ("b", {"task": "b"}),
    ]
