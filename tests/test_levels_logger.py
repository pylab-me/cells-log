from __future__ import annotations

import logging

import pytest

from cells.log.levels import TRACE_LEVEL_NUM, parse_log_level
from cells.log.logger import BoundLogger


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("trace", TRACE_LEVEL_NUM),
        ("debug", logging.DEBUG),
        ("WARNING", logging.WARNING),
        (logging.ERROR, logging.ERROR),
        ("invalid", logging.INFO),
    ],
)
def test_parse_log_level(raw: str | int, expected: int) -> None:
    assert parse_log_level(raw) == expected


def test_bound_logger_merges_bound_and_call_fields() -> None:
    raw_logger = logging.getLogger("bound-test")
    raw_logger.handlers.clear()
    raw_logger.propagate = False
    raw_logger.setLevel(logging.DEBUG)
    records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    raw_logger.addHandler(CaptureHandler())
    BoundLogger(raw_logger).bind(shared="bound", stable=1).info(
        "hello",
        shared="call",
        value=2,
    )

    assert records[0].getMessage() == "hello"
    assert records[0]._fields == {
        "shared": "call",
        "stable": 1,
        "value": 2,
    }


def test_exception_method_preserves_exception_info() -> None:
    raw_logger = logging.getLogger("exception-test")
    raw_logger.handlers.clear()
    raw_logger.propagate = False
    raw_logger.setLevel(logging.ERROR)
    records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    raw_logger.addHandler(CaptureHandler())
    try:
        raise RuntimeError("broken")
    except RuntimeError:
        BoundLogger(raw_logger).exception("failed")

    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is RuntimeError


@pytest.mark.parametrize(
    ("method_name", "level"),
    [
        ("trace", TRACE_LEVEL_NUM),
        ("debug", logging.DEBUG),
        ("success", logging.INFO),
        ("warning", logging.WARNING),
        ("fail", logging.ERROR),
        ("critical", logging.CRITICAL),
    ],
)
def test_bound_logger_methods_use_expected_levels(
    method_name: str,
    level: int,
) -> None:
    raw_logger = logging.getLogger(f"level-test-{method_name}")
    raw_logger.handlers.clear()
    raw_logger.propagate = False
    raw_logger.setLevel(TRACE_LEVEL_NUM)
    records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    raw_logger.addHandler(CaptureHandler())
    getattr(BoundLogger(raw_logger), method_name)("message")

    assert records[0].levelno == level
