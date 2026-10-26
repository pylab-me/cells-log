"""Regression coverage for v0.3.0 safety, context, throughput and lifecycle."""

from __future__ import annotations

import json
import logging
import os
import signal
from pathlib import Path

import pytest

from cells.log import LogConfig
from cells.log.manager import LogManager, LogStage


def _read(path: Path) -> list[dict]:
    """Read complete JSON events from a file sink."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_default_config_preserves_json_fields_and_tracebacks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The default JSON experience must preserve business data and exceptions."""
    manager = LogManager()
    assert LogConfig().use_queue is False
    manager.configure(LogConfig())
    try:
        log = manager.get_logger("v030.default")
        log.info("created", order_id=123)
        try:
            raise ValueError("evidence")
        except ValueError:
            log.exception("failed", order_id=456)
    finally:
        manager.shutdown()
    entries = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert entries[0]["fields"]["order_id"] == 123
    assert entries[1]["fields"]["order_id"] == 456
    assert "ValueError: evidence" in entries[1]["exception"]


def test_redaction_failure_discards_all_original_fields(tmp_path: Path) -> None:
    """The error text and confidential input must not appear in fallback output."""

    def fail(_fields: dict[str, object]) -> dict[str, object]:
        raise ValueError("token-very-secret")

    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            use_queue=False,
            field_processor=fail,
        )
    )
    try:
        manager.get_logger("v030.redaction").info("safe message", password="raw-secret")
    finally:
        manager.shutdown()
    payload = _read(tmp_path / "app.log")[0]
    assert payload["fields"] == {"_field_processor_error": "ValueError"}
    assert "raw-secret" not in json.dumps(payload)
    assert "token-very-secret" not in json.dumps(payload)


def test_third_party_extra_are_in_json_with_reserved_keys_excluded(tmp_path: Path) -> None:
    """stdlib extra is included but LogRecord internals are not business fields."""
    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            manage_root_logger=True,
            use_queue=False,
        )
    )
    try:
        logging.getLogger("v030.external").warning(
            "third party",
            extra={"duration_ms": 25, "custom_status": "ok"},
        )
    finally:
        manager.shutdown()
    fields = _read(tmp_path / "app.log")[0]["fields"]
    assert fields["duration_ms"] == 25
    assert fields["custom_status"] == "ok"
    assert "levelno" not in fields
    assert "trace_id" not in fields


def test_processor_and_json_dumps_run_only_once_across_identical_sinks(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Console+total must share event transformations and JSON serialization."""
    counters = {"processor": 0, "dumps": 0}

    def processor(fields: dict[str, object]) -> dict[str, object]:
        counters["processor"] += 1
        return {**fields, "processor_count": counters["processor"]}

    def dumps(obj: object, **kwargs: object) -> str:
        counters["dumps"] += 1
        kwargs.setdefault("default", str)
        return json.dumps(obj, **kwargs)

    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=True,
            enable_total_file=True,
            field_processor=processor,
            json_dumps=dumps,
            use_queue=False,
        )
    )
    try:
        manager.get_logger("v030.dual").info("sent once", foo="bar")
    finally:
        manager.shutdown()
    assert counters == {"processor": 1, "dumps": 1}
    console_line = capsys.readouterr().err.strip()
    file_line = (tmp_path / "app.log").read_text(encoding="utf-8").strip()
    assert console_line == file_line
    assert json.loads(file_line)["fields"]["processor_count"] == 1


def test_owned_logger_previous_state_restored_on_shutdown(tmp_path: Path) -> None:
    """Acquiring a BoundLogger must not permanently overwrite an existing logger."""
    original = logging.getLogger("v030.state")
    handler = logging.NullHandler()
    old = (list(original.handlers), original.level, original.propagate, original.disabled)
    original.addHandler(handler)
    original.setLevel(logging.WARNING)
    original.propagate = True
    try:
        initial = (list(original.handlers), original.level, original.propagate, original.disabled)
        manager = LogManager()
        manager.configure(LogConfig(enable_console=False, use_queue=False))
        manager.get_logger("v030.state").info("hi")
        assert original.handlers != initial[0]
        manager.shutdown()
        assert (original.handlers, original.level, original.propagate, original.disabled) == initial
    finally:
        original.handlers[:], original.level, original.propagate, original.disabled = old


def test_queue_reports_accepted_delivered_latency(tmp_path: Path) -> None:
    """A successful in-process queue has exact submission and dispatch totals."""
    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            use_queue=True,
            queue_capacity=100,
        )
    )
    try:
        log = manager.get_logger("v030.metrics")
        for i in range(20):
            log.info("metric", sequence=i)
        manager._queue_handler.queue.join()
        stats = manager.get_queue_stats()
        assert stats["accepted_records"] == 20
        assert stats["delivered_records"] == 20
        assert stats["dropped_records"] == 0
        assert stats["delivery_errors"] == 0
        assert stats["max_delivery_latency_ms"] >= stats["mean_delivery_latency_ms"]
    finally:
        manager.shutdown()
    assert len(_read(tmp_path / "app.log")) == 20


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires Unix fork")
@pytest.mark.filterwarnings("ignore:This process.*use of fork:DeprecationWarning")
def test_forked_child_does_not_wait_for_parent_queue(tmp_path: Path) -> None:
    """Inherited queue listener is discarded, and worker can configure its own sinks."""
    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            use_queue=True,
            queue_capacity=20,
        )
    )
    manager.get_logger("v030.fork").info("in parent")
    pid = os.fork()
    if pid == 0:
        # Alarm prevents a stuck listener join from wedging the test process.
        signal.alarm(3)
        try:
            assert manager.stage is LogStage.SHUTDOWN
            assert manager.get_queue_stats()["enabled"] is False
            manager.shutdown()
            manager.configure(
                LogConfig(
                    log_dir=tmp_path,
                    enable_console=False,
                    enable_total_file=True,
                    total_file="child.log",
                    use_queue=True,
                )
            )
            manager.get_logger("v030.child").info("child record")
            manager.shutdown()
            os._exit(0)
        except BaseException:
            os._exit(1)
    try:
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        assert manager.get_queue_stats()["enabled"] is True
    finally:
        manager.shutdown()
    assert [item["message"] for item in _read(tmp_path / "child.log")] == ["child record"]
