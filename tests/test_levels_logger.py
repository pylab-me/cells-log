"""Level parsing and structured logger record semantics."""

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
    """Resolve standard levels, TRACE and the invalid-name fallback."""
    assert parse_log_level(raw) == expected


def test_bound_logger_merges_bound_and_call_fields() -> None:
    """Give call fields precedence without discarding stable bound fields."""
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
    """Retain the current exception type and traceback on the record."""
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
    """Map each method and convenience alias to its intended numeric level."""
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


@pytest.mark.parametrize(
    ("method_name", "expected_level"),
    [
        ("trace", TRACE_LEVEL_NUM),
        ("debug", logging.DEBUG),
        ("info", logging.INFO),
        ("success", logging.INFO),
        ("warning", logging.WARNING),
        ("warn", logging.WARNING),
        ("error", logging.ERROR),
        ("fail", logging.ERROR),
        ("critical", logging.CRITICAL),
        ("exception", logging.ERROR),
    ],
)
def test_bound_logger_internal_parameters_do_not_consume_structured_fields(
    method_name: str, expected_level: int
) -> None:
    """Keep business fields separate from internal level, message and exception controls."""
    raw_logger = logging.Logger("field-collision-test", TRACE_LEVEL_NUM)
    records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    raw_logger.addHandler(CaptureHandler())
    fields = {
        "level": "business-level",
        "exc_info": "business-exception",
        "_level": "underscore-level",
        "_message": "underscore-message",
        "_exc_info": "underscore-exception",
        "fields": "business-fields",
    }
    logger = BoundLogger(raw_logger).bind(level="bound-level", stable="bound")
    try:
        raise RuntimeError("real exception")
    except RuntimeError:
        getattr(logger, method_name)("actual message", **fields)

    assert len(records) == 1
    record = records[0]
    assert record.getMessage() == "actual message"
    assert record.levelno == expected_level
    assert record._fields == {**fields, "stable": "bound"}
    if method_name == "exception":
        assert record.exc_info is not None
        assert record.exc_info[0] is RuntimeError
        assert str(record.exc_info[1]) == "real exception"
    else:
        assert record.exc_info is False


def test_invalid_log_level_emits_warning(capsys: pytest.CaptureFixture[str]) -> None:
    """Describe the filtering consequence and accepted level inputs."""
    assert parse_log_level("INF0") == logging.INFO
    assert capsys.readouterr().err == (
        "cells-log: WARNING level='INF0' is invalid; "
        "the effective value is 'INFO', so DEBUG and TRACE records may be filtered out. "
        "Valid values: TRACE, DEBUG, INFO, WARNING, WARN, ERROR, CRITICAL, FATAL, NOTSET, "
        "or an integer.\n"
    )
