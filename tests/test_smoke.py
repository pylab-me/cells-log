from __future__ import annotations

from pathlib import Path

from cells.log import (
    LogConfig,
    configure_logging,
    get_logger,
    reset_trace_id,
    set_trace_id,
    shutdown_logging,
)


def test_module_and_total_logs(tmp_path: Path) -> None:
    configure_logging(
        LogConfig(
            app_name="demo",
            level="DEBUG",
            format="json",
            log_dir=tmp_path,
            enable_console=False,
            enable_module_file=True,
            enable_total_file=True,
            total_file="total.log",
            module_name_prefixes=("demo",),
            use_queue=False,
        )
    )
    trace_token = set_trace_id("trace-test")
    get_logger("demo.worker").info("hello", value=1)
    reset_trace_id(trace_token)
    shutdown_logging()

    total_text = (tmp_path / "total.log").read_text(encoding="utf-8")
    module_text = (tmp_path / "modules" / "demo_worker.log").read_text(encoding="utf-8")

    assert "hello" in total_text
    assert "trace-test" in total_text
    assert "hello" in module_text
