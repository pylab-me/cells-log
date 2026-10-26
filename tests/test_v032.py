"""Queue reliability contracts introduced in cells-log v0.3.2."""

from __future__ import annotations

import ast
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cells.log import LogConfig, flush_logging, get_queue_stats
from cells.log.handlers import LocalQueueHandler, make_log_queue
from cells.log.manager import LogManager


def _record(message: str, level: int = logging.INFO) -> logging.LogRecord:
    """Create a standalone logging record for deterministic saturation tests."""
    return logging.LogRecord("queue.worker", level, __file__, 1, message, (), None)


class _Messages(logging.Handler):
    """Record delivered messages without disk or stdout contention."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _queue_handler(policy: str, *, fallback_min_level: int | None = None):
    """Create an unconsumed bounded queue to deterministically saturate it."""
    sink = _Messages()
    handler = LocalQueueHandler(
        make_log_queue(1),
        full_policy=policy,
        block_timeout=0.01,
        fallback_handlers=(sink,),
        fallback_min_level=fallback_min_level,
    )
    return handler, sink


def test_saturation_drop_counts_exact_loss_and_pending() -> None:
    handler, sink = _queue_handler("drop")
    handler.enqueue(_record("first"))
    handler.enqueue(_record("second"))
    stats = handler.stats()
    assert stats["submitted_records"] == 2
    assert stats["accepted_records"] == 1
    assert stats["pending_records"] == 1
    assert stats["dropped_records"] == 1
    assert stats["failed_or_dropped_records"] == 1
    assert stats["fallback_records"] == 0
    assert sink.messages == []


def test_priority_fallback_only_on_overflow_and_at_or_above_level() -> None:
    handler, sink = _queue_handler("drop", fallback_min_level=logging.ERROR)
    handler.enqueue(_record("first", logging.INFO))
    handler.enqueue(_record("low", logging.WARNING))
    handler.enqueue(_record("important", logging.ERROR))
    handler.enqueue(_record("critical", logging.CRITICAL))
    stats = handler.stats()
    assert stats["submitted_records"] == 4
    assert stats["accepted_records"] == 1
    assert stats["dropped_records"] == 1
    assert stats["fallback_records"] == 2
    assert stats["fallback_delivered_records"] == 2
    assert stats["fallback_delivery_errors"] == 0
    assert stats["failed_or_dropped_records"] == 1
    assert sink.messages == ["important", "critical"]


def test_priority_fallback_after_block_timeout() -> None:
    handler, sink = _queue_handler("block", fallback_min_level=logging.ERROR)
    handler.enqueue(_record("first"))
    handler.enqueue(_record("priority", logging.ERROR))
    handler.enqueue(_record("normal"))
    stats = handler.stats()
    assert stats["block_timeouts"] == 2
    assert stats["dropped_records"] == 1
    assert stats["fallback_delivered_records"] == 1
    assert sink.messages == ["priority"]


def test_sync_fallback_detected_handler_error_and_surviving_sink(monkeypatch) -> None:
    class _Failing(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise OSError("simulated failure")

    handler, sink = _queue_handler("sync_fallback")
    handler._fallback_handlers = (_Failing(), sink)
    monkeypatch.setattr(logging, "raiseExceptions", False)
    handler.enqueue(_record("first"))
    handler.enqueue(_record("second"))
    stats = handler.stats()
    assert stats["fallback_records"] == 1
    assert stats["fallback_delivered_records"] == 0
    assert stats["fallback_delivery_errors"] == 1
    assert stats["failed_or_dropped_records"] == 1
    assert sink.messages == ["second"]


def test_public_flush_synchronously_flushes_module_files(tmp_path: Path) -> None:
    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=False,
            enable_module_file=True,
        )
    )
    try:
        manager.get_logger("app.orders").info("saved", item=1)
        router = manager._handlers[0]
        assert len(router._handlers) == 1
        child = next(iter(router._handlers.values()))
        invoked = []
        original_flush = child.flush

        def tracked_flush() -> None:
            invoked.append(True)
            original_flush()

        child.flush = tracked_flush
        assert manager.flush(timeout=1.0) is True
        assert invoked
        assert (
            json.loads((tmp_path / "modules" / "app_orders.log").read_text())["message"] == "saved"
        )
    finally:
        manager.shutdown()


def test_public_flush_queue_waits_for_listener_and_flushes_file(tmp_path: Path) -> None:
    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            use_queue=True,
            queue_capacity=64,
        )
    )
    try:
        logger = manager.get_logger("v032.flush")
        for index in range(20):
            logger.info("event", index=index)
        assert manager.flush(timeout=3.0)
        stats = manager.get_queue_stats()
        assert stats["pending_records"] == 0
        assert stats["accepted_records"] == 20
        assert stats["delivered_records"] == 20
        path = tmp_path / "app.log"
        assert len(path.read_text().splitlines()) == 20
    finally:
        manager.shutdown()


def test_flush_timeout_is_bounded_and_retryable(tmp_path: Path) -> None:
    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            use_queue=True,
            queue_capacity=2,
        )
    )
    entered = threading.Event()
    resume = threading.Event()
    sink = manager._handlers[0]
    original_emit = sink.emit

    def blocking_emit(record: logging.LogRecord) -> None:
        entered.set()
        if not resume.wait(5):
            raise TimeoutError("test listener still blocked")
        original_emit(record)

    sink.emit = blocking_emit
    try:
        manager.get_logger("v032.timeout").info("queued")
        assert entered.wait(2)
        start = time.monotonic()
        assert manager.flush(timeout=0.03) is False
        assert time.monotonic() - start < 1.0
        assert manager.get_queue_stats()["pending_records"] == 1
        resume.set()
        assert manager.flush(timeout=3.0) is True
        assert manager.get_queue_stats()["pending_records"] == 0
    finally:
        resume.set()
        manager.shutdown()


def test_flush_propagates_sink_failure_and_validates_timeout(tmp_path: Path) -> None:
    manager = LogManager()
    manager.configure(LogConfig(enable_console=False, enable_total_file=False))
    try:
        with pytest.raises(ValueError):
            manager.flush(timeout=-0.1)
        manager.configure(
            LogConfig(
                log_dir=tmp_path,
                enable_console=False,
                enable_total_file=True,
            )
        )

        def broken_flush() -> None:
            raise OSError("disk sync failed")

        sink = manager._handlers[0]
        original_flush = sink.flush
        try:
            sink.flush = broken_flush
            with pytest.raises(OSError, match="disk sync failed"):
                manager.flush(timeout=1.0)
        finally:
            sink.flush = original_flush
    finally:
        manager.shutdown()


def test_multithread_logging_and_reconfiguration_no_loss(tmp_path: Path) -> None:
    manager = LogManager()
    common = dict(
        log_dir=tmp_path,
        enable_console=False,
        enable_total_file=True,
        use_queue=True,
        queue_capacity=1000,
        queue_full_policy="block",
        queue_block_timeout=1.0,
    )
    manager.configure(LogConfig(**common, total_file="before.log"))
    started = threading.Event()

    def produce(worker: int) -> None:
        logger = manager.get_logger("v032.stress")
        started.wait(5)
        for value in range(100):
            logger.info("record", worker=worker, value=value)
            if value % 10 == 0:
                time.sleep(0)

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(produce, index) for index in range(4)]
            started.set()
            manager.configure(LogConfig(**common, total_file="after.log"))
            for future in futures:
                future.result(timeout=10)
        assert manager.flush(timeout=5.0)
        assert manager.get_queue_stats()["dropped_records"] == 0
    finally:
        manager.shutdown()
    all_lines = [
        json.loads(line)
        for filename in ("before.log", "after.log")
        for line in (tmp_path / filename).read_text().splitlines()
    ]
    identities = {(x["fields"]["worker"], x["fields"]["value"]) for x in all_lines}
    assert len(all_lines) == len(identities) == 400


def test_python310_parser_accepts_every_source_file() -> None:
    root = Path(__file__).resolve().parents[1]
    for source in (root / "cells" / "log").glob("*.py"):
        ast.parse(source.read_text(encoding="utf-8"), filename=str(source), feature_version=(3, 10))


def test_public_flush_callable_exposed() -> None:
    assert callable(flush_logging)
    assert "submitted_records" in get_queue_stats()
