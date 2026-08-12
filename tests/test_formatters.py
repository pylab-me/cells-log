from __future__ import annotations

import json
import logging

from cells.log.config import LogConfig
from cells.log.formatters import JsonLineFormatter


def _record() -> logging.LogRecord:
    record = logging.LogRecord(
        name="demo.worker",
        level=logging.ERROR,
        pathname="/app/worker.py",
        lineno=42,
        msg="failed",
        args=(),
        exc_info=None,
    )
    record.trace_id = "trace"
    record._context_fields = {"shared": "context", "request_id": "r1"}
    record._fields = {"shared": "bound", "value": object()}
    return record


def test_json_formatter_merges_fields_with_bound_precedence() -> None:
    payload = json.loads(JsonLineFormatter(LogConfig()).format(_record()))

    assert payload["trace_id"] == "trace"
    assert payload["fields"]["shared"] == "bound"
    assert payload["fields"]["request_id"] == "r1"
    assert isinstance(payload["fields"]["value"], str)


def test_field_processor_failure_is_isolated() -> None:
    def fail(_fields: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("processor failed")

    payload = json.loads(JsonLineFormatter(LogConfig(field_processor=fail)).format(_record()))

    assert payload["fields"]["_field_processor_error"] == "RuntimeError: processor failed"


def test_exception_formatter_includes_traceback() -> None:
    try:
        raise ValueError("broken")
    except ValueError:
        record = logging.LogRecord(
            name="demo",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failure",
            args=(),
            exc_info=__import__("sys").exc_info(),
        )

    payload = json.loads(JsonLineFormatter(LogConfig()).format(record))

    assert "ValueError: broken" in payload["exception"]
