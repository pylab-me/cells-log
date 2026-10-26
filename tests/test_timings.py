"""Request timing collector lifecycle, logging and concurrency behavior."""

from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import local
from typing import Any

import pytest

from cells.log import LogConfig
from cells.log.context import bind_log_context, reset_log_context
from cells.log.formatters import ContextFilter, JsonLineFormatter
from cells.log.logger import BoundLogger


@pytest.fixture
def timing_logger() -> tuple[BoundLogger, list[logging.LogRecord]]:
    """Capture structured summaries without touching the global logger registry."""
    records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    raw_logger = logging.Logger("timing-test", logging.DEBUG)
    handler = CaptureHandler()
    handler.addFilter(ContextFilter())
    raw_logger.addHandler(handler)
    return BoundLogger(raw_logger), records


def set_clock(monkeypatch: pytest.MonkeyPatch, *values: float) -> None:
    """Install a finite clock sequence so extra reads fail the test."""
    clock = iter(values)
    monkeypatch.setattr("cells.log.timings.perf_counter", lambda: next(clock))


def test_timings_accumulate_duplicates_and_declare_missing_metrics(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep exact milliseconds, accumulated stages and unexecuted declarations."""
    logger, records = timing_logger
    set_clock(monkeypatch, 10, 10.1, 10.125, 10.2, 10.25, 10.5)
    with logger.timings("token.request", metrics=("db.acquire_ms", "unused_ms")) as timing:
        with timing.measure("db.acquire_ms"):
            pass
        with timing.measure("db.acquire_ms"):
            pass

    assert len(records) == 1
    assert records[0].getMessage() == "token.request"
    assert records[0].levelno == logging.DEBUG
    assert records[0]._fields == {
        "db.acquire_ms": pytest.approx(75),
        "unused_ms": None,
        "token.request.total_ms": pytest.approx(500),
    }
    assert records[0].funcName == (
        test_timings_accumulate_duplicates_and_declare_missing_metrics.__name__
    )


@pytest.mark.parametrize("level", ["trace", logging.WARNING, 17])
def test_timings_use_requested_levels(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]], level: str | int
) -> None:
    """Support named, TRACE and custom integer levels."""
    logger, records = timing_logger
    logger.raw_logger.setLevel(1)
    with logger.timings("event", level=level):
        pass
    assert records[0].levelno == {"trace": 5}.get(level, level)


def test_timings_preserve_context_binding_fields_and_metric_precedence(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Merge context, binding, call fields and generated timing values in order."""
    logger, records = timing_logger
    set_clock(monkeypatch, 0, 0.125)
    token = bind_log_context(shared="context", from_context=1)
    try:
        with logger.bind(shared="bound", from_binding=2).timings(
            "request",
            metrics=("unused_ms",),
            shared="call",
            fields="business",
            exc_info="business",
            **{"unused_ms": 999, "request.total_ms": 999},
        ):
            pass
    finally:
        reset_log_context(token)

    formatter = JsonLineFormatter(LogConfig())
    payload = json.loads(formatter.format(records[0]))
    assert payload["fields"] == {
        "shared": "call",
        "from_context": 1,
        "from_binding": 2,
        "fields": "business",
        "exc_info": "business",
        "unused_ms": None,
        "request.total_ms": 125.0,
    }
    assert "exception" not in payload


@pytest.mark.parametrize("error", [RuntimeError("secret-code"), KeyboardInterrupt("secret-code")])
def test_timings_preserve_base_exception_without_logging_its_text(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]],
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    """Measure failed stages and propagate the identical exception object."""
    logger, records = timing_logger
    set_clock(monkeypatch, 0, 0.1, 0.3, 0.4)
    with pytest.raises(type(error)) as caught:
        with logger.timings("request") as timing:
            with timing.measure("lookup_ms"):
                raise error
    assert caught.value is error
    assert len(records) == 1
    assert records[0]._fields["lookup_ms"] == pytest.approx(200)
    assert records[0]._fields["request.total_ms"] == pytest.approx(400)
    assert not records[0].exc_info
    assert "secret-code" not in JsonLineFormatter(LogConfig()).format(records[0])


def test_disabled_timings_never_read_clock_or_emit(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid diagnostic clock overhead even if logging is enabled mid-request."""
    logger, records = timing_logger
    logger.raw_logger.setLevel(logging.INFO)

    def forbidden_clock() -> float:
        raise AssertionError("disabled timing must not read the clock")

    monkeypatch.setattr("cells.log.timings.perf_counter", forbidden_clock)
    with logger.timings("request") as timing:
        with timing.measure("db_ms"):
            logger.raw_logger.setLevel(logging.DEBUG)
    assert records == []


def test_timings_decide_enablement_at_entry(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow constructing a collector before its level is enabled."""
    logger, records = timing_logger
    logger.raw_logger.setLevel(logging.INFO)
    collector = logger.timings("request")
    logger.raw_logger.setLevel(logging.DEBUG)
    set_clock(monkeypatch, 0, 1)
    with collector:
        pass
    assert records[0]._fields["request.total_ms"] == 1000


def test_timings_lifecycle_and_reserved_total(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]],
) -> None:
    """Reject reuse and measurements made outside an active scope."""
    logger, records = timing_logger
    with pytest.raises(ValueError, match="generated total"):
        logger.timings("request", metrics=("request.total_ms",))
    collector = logger.timings("request")
    with pytest.raises(RuntimeError, match="active"):
        collector.measure("db_ms")
    with collector:
        with pytest.raises(ValueError, match="generated total"):
            collector.measure("request.total_ms")
        measurement = collector.measure("db_ms")
        with measurement:
            pass
        with pytest.raises(RuntimeError, match="single-use"):
            with measurement:
                pass
        delayed = collector.measure("delayed_ms")
    with pytest.raises(RuntimeError, match="active"):
        collector.measure("db_ms")
    with pytest.raises(RuntimeError, match="active"):
        delayed.__enter__()
    with pytest.raises(RuntimeError, match="single-use"):
        collector.__enter__()
    assert len(records) == 1


def test_outstanding_measurements_freeze_without_mutating_summary(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Include outstanding stages up to exit and keep the emitted snapshot stable."""
    logger, records = timing_logger
    set_clock(monkeypatch, 0, 0.1, 0.5)
    with logger.timings("request") as timing:
        pending = timing.measure("pending_ms")
        pending.__enter__()
    snapshot = dict(records[0]._fields)
    pending.__exit__(None, None, None)
    assert (
        records[0]._fields
        == snapshot
        == {
            "pending_ms": 400.0,
            "request.total_ms": 500.0,
        }
    )


def test_async_cancellation_records_summary_and_original_exception(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Support async scopes and cancellation while retaining caller location."""
    logger, records = timing_logger
    set_clock(monkeypatch, 0, 0.1, 0.2, 0.5)

    async def cancelled_request() -> None:
        async with logger.timings("request") as timing:
            async with timing.measure("wait_ms"):
                asyncio.current_task().cancel()
                await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled_request())
    assert len(records) == 1
    assert records[0]._fields["wait_ms"] == pytest.approx(100)
    assert records[0].funcName == "cancelled_request"
    assert not records[0].exc_info


def test_explicit_collector_accumulates_across_thread_pool(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protect shared stage accumulation from concurrent worker updates."""
    logger, records = timing_logger
    thread_clock = local()

    def clock() -> float:
        thread_clock.value = getattr(thread_clock, "value", 0.0) + 0.001
        return thread_clock.value

    monkeypatch.setattr("cells.log.timings.perf_counter", clock)
    with logger.timings("request") as timing:

        def worker() -> None:
            for _ in range(100):
                with timing.measure("db_ms"):
                    pass

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker) for _ in range(16)]
            for future in futures:
                future.result()
    assert len(records) == 1
    assert records[0]._fields["db_ms"] == pytest.approx(1600)


def test_async_requests_and_to_thread_keep_collectors_isolated(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]],
) -> None:
    """Keep separate request handles isolated across tasks and thread handoffs."""
    logger, records = timing_logger

    async def request(request_id: str) -> None:
        token = bind_log_context(request_id=request_id)
        try:
            async with logger.timings("request", metrics=("unused_ms",)) as timing:

                def worker() -> None:
                    with timing.measure(f"{request_id}_ms"):
                        pass

                await asyncio.to_thread(worker)
        finally:
            reset_log_context(token)

    async def run_requests() -> None:
        await asyncio.gather(request("a"), request("b"))

    asyncio.run(run_requests())
    assert len(records) == 2
    for record in records:
        request_id = record._context_fields["request_id"]
        assert set(record._fields) == {"unused_ms", f"{request_id}_ms", "request.total_ms"}


def test_sink_failure_does_not_replace_business_exception(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain business exceptions while surfacing standalone sink failures."""
    logger, _ = timing_logger

    def failing_log(*args: Any, **kwargs: Any) -> None:
        raise OSError("sink unavailable")

    monkeypatch.setattr(logger.raw_logger, "log", failing_log)
    error = RuntimeError("business failure")
    with pytest.raises(RuntimeError) as caught:
        with logger.timings("request"):
            raise error
    assert caught.value is error
    with pytest.raises(OSError, match="sink unavailable"):
        with logger.timings("request"):
            pass


def test_nested_stages_keep_inclusive_durations_independent_from_total(
    timing_logger: tuple[BoundLogger, list[logging.LogRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow nested phase sums to exceed the independent request wall time."""
    logger, records = timing_logger
    set_clock(monkeypatch, 0, 0.1, 0.2, 0.8, 0.9, 1.0)
    with logger.timings("request") as timing:
        with timing.measure("db.total_ms"):
            with timing.measure("db.lookup_ms"):
                pass
    fields = records[0]._fields
    assert fields["db.total_ms"] == pytest.approx(800)
    assert fields["db.lookup_ms"] == pytest.approx(600)
    assert fields["request.total_ms"] == pytest.approx(1000)
    assert fields["db.total_ms"] + fields["db.lookup_ms"] > fields["request.total_ms"]
