from __future__ import annotations

import logging

from cells.log.config import LogConfig
from cells.log.formatters import ConsoleTextFormatter


def _record(level: int = logging.INFO) -> logging.LogRecord:
    record = logging.LogRecord(
        name="demo",
        level=level,
        pathname=__file__,
        lineno=1,
        msg="message",
        args=(),
        exc_info=None,
    )
    record.trace_id = "trace"
    record._fields = {"value": 1}
    return record


def test_console_formatter_includes_fields() -> None:
    formatter = ConsoleTextFormatter(
        LogConfig(),
        include_fields=True,
    )

    rendered = formatter.format(_record())

    assert "[trace]" in rendered
    assert "demo: message" in rendered
    assert '"value": 1' in rendered


def test_console_error_can_point_to_detail_sink() -> None:
    formatter = ConsoleTextFormatter(
        LogConfig(),
        include_fields=True,
        simplify_console_errors=True,
        detail_sink_enabled=True,
    )

    rendered = formatter.format(_record(logging.ERROR))

    assert rendered.endswith("(details in log file)")
    assert '"value": 1' not in rendered
