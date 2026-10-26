from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Event, Thread

import pytest

from cells.log import (
    LogConfig,
    configure_logging,
    get_logger,
    get_queue_stats,
    shutdown_logging,
)
from cells.log.manager import LogManager


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
        "accepted_records": 0,
        "delivered_records": 0,
        "fallback_records": 0,
        "delivery_errors": 0,
        "max_depth_sampled": 0,
        "mean_delivery_latency_ms": 0.0,
        "max_delivery_latency_ms": 0.0,
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


@pytest.mark.parametrize("old_queue", [False, True])
@pytest.mark.parametrize("new_queue", [False, True])
@pytest.mark.parametrize("manage_root", [False, True])
def test_in_flight_logger_uses_live_sink_after_reconfiguration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old_queue: bool,
    new_queue: bool,
    manage_root: bool,
) -> None:
    """Deliver a previously selected handler to the new live sink exactly once."""
    manager = LogManager()
    settings = {
        "log_dir": tmp_path,
        "enable_console": False,
        "enable_total_file": True,
        "manage_root_logger": manage_root,
    }
    manager.configure(LogConfig(**settings, total_file="old.log", use_queue=old_queue))
    log = manager.get_logger("in-flight")
    entry = logging.getLogger() if manage_root else log.raw_logger
    handler = entry.handlers[0]
    handle = handler.handle
    selected = Event()
    resume = Event()

    def paused_handle(record: logging.LogRecord) -> bool:
        # Pause after logging selected its handler, before delivery takes a lock.
        selected.set()
        assert resume.wait(5)
        return handle(record)

    monkeypatch.setattr(handler, "handle", paused_handle)
    producer = Thread(target=lambda: log.info("during-switch"), daemon=True)
    producer.start()
    try:
        assert selected.wait(5)
        manager.configure(LogConfig(**settings, total_file="new.log", use_queue=new_queue))
    finally:
        resume.set()
        producer.join(5)
        manager.shutdown()

    assert not producer.is_alive()
    assert (tmp_path / "old.log").read_text(encoding="utf-8") == ""
    messages = [
        json.loads(line)["message"]
        for line in (tmp_path / "new.log").read_text(encoding="utf-8").splitlines()
    ]
    assert messages == ["during-switch"]


def test_pending_queue_rotates_before_same_file_is_reconfigured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drain rotating writes before a replacement opens the same Windows file."""
    manager = LogManager()
    writing = Event()
    resume = Event()
    draining = Event()
    failures: list[BaseException] = []

    def pause_first_record(fields: dict) -> dict:
        writing.set()
        assert resume.wait(5)
        return fields

    config = LogConfig(
        log_dir=tmp_path,
        enable_console=False,
        enable_total_file=True,
        use_queue=True,
        rotation="size",
        size_limit=1,
        retention=10,
        field_processor=pause_first_record,
    )
    manager.configure(config)
    log = manager.get_logger("pending")
    log.info("first")
    assert writing.wait(5)
    log.info("second")
    drain_queue = manager._drain_queue

    def observed_drain() -> None:
        draining.set()
        drain_queue()

    monkeypatch.setattr(manager, "_drain_queue", observed_drain)

    def reconfigure() -> None:
        try:
            manager.configure(config)
        except BaseException as exc:
            failures.append(exc)

    worker = Thread(target=reconfigure, daemon=True)
    worker.start()
    try:
        assert draining.wait(5)
    finally:
        resume.set()
        worker.join(5)
        manager.shutdown()

    assert not worker.is_alive()
    assert failures == []
    log_files = list(tmp_path.glob("app.log*"))
    messages = [
        json.loads(line)["message"]
        for path in log_files
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert sorted(messages) == ["first", "second"]
