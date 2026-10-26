"""v0.3.1: hot-path module routing and observable built-in sink failures."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from cells.log import LogConfig
from cells.log.formatters import JsonLineFormatter
from cells.log.handlers import ModuleFileRouterHandler, create_rotating_file_handler
from cells.log.manager import LogManager


def _record(name: str, message: str) -> logging.LogRecord:
    """Build an event without relying on the global logger registry."""
    return logging.LogRecord(name, logging.INFO, __file__, 1, message, (), None)


def test_module_name_cache_bounded_preserves_aliases_and_overflow(tmp_path: Path) -> None:
    """Repeated names skip routing work; dynamic names cannot grow cache without bound."""
    config = LogConfig(
        log_dir=tmp_path,
        rotation="size",
        size_limit="1MB",
        max_module_handlers=2,
        enable_console=False,
    ).normalized()
    router = ModuleFileRouterHandler(config, JsonLineFormatter(config), logging.INFO)
    try:
        for name in ["svc.api", "svc_api", "svc.api", "svc.tasks", "svc.other"]:
            router.handle(_record(name, name))
        assert len(router._name_cache) <= config.max_module_handlers
        assert router._name_cache["svc.api"] is router._name_cache["svc_api"]
        assert len(router._handlers) <= config.max_module_handlers + 1
    finally:
        router.close()
    files = tmp_path / "modules"
    assert [
        json.loads(line)["logger"] for line in (files / "svc_api.log").read_text().splitlines()
    ] == [
        "svc.api",
        "svc_api",
        "svc.api",
    ]
    assert [
        json.loads(line)["logger"] for line in (files / "svc_tasks.log").read_text().splitlines()
    ] == ["svc.tasks"]
    assert [
        json.loads(line)["logger"] for line in (files / "overflow.log").read_text().splitlines()
    ] == ["svc.other"]
    assert not router._name_cache


def test_builtin_console_sink_failure_visible_to_queue_stats(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """stdio write errors must not be counted as successfully dispatched."""

    class FailingStream:
        def write(self, _message: str) -> None:
            raise OSError("simulated closed pipe")

        def flush(self) -> None:
            pass

    manager = LogManager()
    manager.configure(LogConfig(enable_console=True, use_queue=True))
    monkeypatch.setattr(logging, "raiseExceptions", False)
    try:
        console = manager._handlers[0]
        original = console.stream
        console.stream = FailingStream()
        manager.get_logger("v031.write").info("cannot be written")
        manager._queue_handler.queue.join()
        console.stream = original
        manager.get_logger("v031.write").info("later record succeeds")
        manager._queue_handler.queue.join()
        stats = manager.get_queue_stats()
        assert stats["accepted_records"] == 2
        assert stats["delivered_records"] == 1
        assert stats["delivery_errors"] == 1
    finally:
        manager.shutdown()


def test_builtin_rotating_file_handler_marks_caught_io_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RotatingFileHandler internally swallows failures via handleError."""
    config = LogConfig(rotation="size", size_limit=1).normalized()
    handler = create_rotating_file_handler(
        tmp_path / "out.log",
        config,
        JsonLineFormatter(config),
        logging.INFO,
    )
    monkeypatch.setattr(logging, "raiseExceptions", False)

    def fail_rollover() -> None:
        raise OSError("rotation denied")

    try:
        handler.handle(_record("v031.file", "fills the initially empty log"))
        monkeypatch.setattr(handler, "doRollover", fail_rollover)
        record = _record("v031.file", "cannot rotate")
        handler.handle(record)
        assert record._cells_sink_error is True
    finally:
        handler.close()


def test_module_router_marks_routing_failure(tmp_path: Path, monkeypatch) -> None:
    """Creation errors are visible even when router.handleError suppresses them."""
    config = LogConfig(log_dir=tmp_path).normalized()
    router = ModuleFileRouterHandler(config, JsonLineFormatter(config), logging.INFO)
    monkeypatch.setattr(logging, "raiseExceptions", False)

    def fail_handler(_name: str):
        raise OSError("disk full")

    monkeypatch.setattr(router, "_get_or_create_handler", fail_handler)
    record = _record("v031.fail", "unavailable")
    router.handle(record)
    assert record._cells_sink_error is True
    router.close()


def test_module_router_cached_name_does_not_recalculate_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A hot, stable channel bypasses path normalization after first use."""
    config = LogConfig(log_dir=tmp_path, max_module_handlers=2).normalized()
    router = ModuleFileRouterHandler(config, JsonLineFormatter(config), logging.INFO)
    calls = []
    original = router._module_path

    def count(module_name: str) -> Path:
        calls.append(module_name)
        return original(module_name)

    monkeypatch.setattr(router, "_module_path", count)
    try:
        for index in range(5):
            router.handle(_record("stable.worker", str(index)))
        assert calls == ["stable.worker"]
    finally:
        router.close()


def test_queue_marks_partial_dual_sink_failure(tmp_path: Path, monkeypatch) -> None:
    """A successful file must not mask the console failure for the same event."""

    class BrokenStream:
        def write(self, _message: str) -> None:
            raise OSError("broken output")

        def flush(self) -> None:
            pass

    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=True,
            enable_total_file=True,
            use_queue=True,
        )
    )
    monkeypatch.setattr(logging, "raiseExceptions", False)
    try:
        stream = manager._handlers[0].stream
        manager._handlers[0].stream = BrokenStream()
        manager.get_logger("v031.partial").info("file copy survives")
        manager._queue_handler.queue.join()
        manager._handlers[0].stream = stream
        stats = manager.get_queue_stats()
        assert stats["accepted_records"] == 1
        assert stats["delivery_errors"] == 1
        assert stats["delivered_records"] == 0
    finally:
        manager.shutdown()
    events = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert '"message": "file copy survives"' in events
