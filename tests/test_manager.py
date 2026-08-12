from __future__ import annotations

import json
from pathlib import Path

from cells.log import (
    LogConfig,
    configure_logging,
    get_logger,
    get_queue_stats,
    shutdown_logging,
)


def test_public_queue_stats_report_active_configuration(tmp_path: Path) -> None:
    configure_logging(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            use_queue=True,
            queue_capacity=7,
        )
    )

    stats = get_queue_stats()
    get_logger("demo").info("queued")
    shutdown_logging()

    assert stats["enabled"] is True
    assert stats["capacity"] == 7
    assert stats["dropped_records"] == 0
    assert json.loads((tmp_path / "app.log").read_text(encoding="utf-8"))["message"] == "queued"
    assert get_queue_stats() == {
        "enabled": False,
        "capacity": 0,
        "depth": 0,
        "dropped_records": 0,
    }


def test_reconfigure_flushes_old_queue_before_switching_files(
    tmp_path: Path,
) -> None:
    configure_logging(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            total_file="first.log",
            use_queue=True,
            queue_capacity=2,
        )
    )
    get_logger("demo").info("first")

    configure_logging(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            total_file="second.log",
            use_queue=True,
            queue_capacity=2,
        )
    )
    get_logger("demo").info("second")
    shutdown_logging()

    assert "first" in (tmp_path / "first.log").read_text(encoding="utf-8")
    assert "second" in (tmp_path / "second.log").read_text(encoding="utf-8")
