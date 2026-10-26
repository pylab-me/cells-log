"""Severity eligibility and the disabled structured logging path."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from unittest.mock import Mock

import pytest

from cells.log import LogConfig, configure_logging, get_logger
from cells.log.levels import TRACE_LEVEL_NUM
from cells.log.logger import BoundLogger


@pytest.fixture
def raw_logger() -> Iterator[logging.Logger]:
    """Isolate logger state and restore the process-wide disable threshold."""
    raw = logging.getLogger("enabled-tests.child")
    old_level, old_disabled = raw.level, raw.disabled
    old_disable = logging.root.manager.disable
    raw.setLevel(logging.DEBUG)
    raw.disabled = False
    logging.disable(logging.NOTSET)
    try:
        yield raw
    finally:
        raw.setLevel(old_level)
        raw.disabled = old_disabled
        logging.disable(old_disable)


@pytest.mark.parametrize(
    ("level", "enabled"),
    [
        (" trace ", False),
        ("debug", True),
        ("WARN", True),
        ("FATAL", True),
        ("NOTSET", False),
        (TRACE_LEVEL_NUM, False),
        (logging.INFO, True),
        (17, True),
    ],
)
def test_is_enabled_for_parses_names_and_custom_integers(
    raw_logger: logging.Logger, level: str | int, enabled: bool
) -> None:
    """Use existing level parsing without rejecting custom numeric severities."""
    assert BoundLogger(raw_logger).is_enabled_for(level) is enabled


def test_is_enabled_for_retains_invalid_name_fallback(
    raw_logger: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    """Warn for unknown names and evaluate the documented INFO fallback."""
    raw_logger.setLevel(logging.WARNING)
    assert not BoundLogger(raw_logger).is_enabled_for("INF0")
    assert "the effective value is 'INFO'" in capsys.readouterr().err


def test_is_enabled_for_tracks_parent_level_and_disable_state(
    raw_logger: logging.Logger,
) -> None:
    """Follow inherited levels and invalidation of cached stdlib decisions."""
    parent = logging.getLogger("enabled-tests")
    old_level = parent.level
    try:
        raw_logger.setLevel(logging.NOTSET)
        parent.setLevel(logging.DEBUG)
        logger = BoundLogger(raw_logger)
        assert logger.is_enabled_for("DEBUG")
        parent.setLevel(logging.WARNING)
        assert not logger.is_enabled_for("DEBUG")
        assert logger.is_enabled_for("ERROR")
        raw_logger.disabled = True
        assert not logger.is_enabled_for("ERROR")
        raw_logger.disabled = False
        assert logger.is_enabled_for("ERROR")
        logging.disable(logging.ERROR)
        assert not logger.is_enabled_for("ERROR")
        assert logger.is_enabled_for("CRITICAL")
        logging.disable(logging.NOTSET)
        assert logger.is_enabled_for("ERROR")
    finally:
        parent.setLevel(old_level)


def test_existing_wrapper_tracks_manager_reconfiguration() -> None:
    """Reconfiguration changes eligibility on wrappers already held by callers."""
    configure_logging(LogConfig(level="INFO", enable_console=False, use_queue=False))
    logger = get_logger("enabled-managed")
    assert not logger.is_enabled_for("DEBUG")
    configure_logging(LogConfig(level="TRACE", enable_console=False, use_queue=False))
    assert logger.is_enabled_for("TRACE")
    configure_logging(LogConfig(level="ERROR", enable_console=False, use_queue=False))
    assert not logger.is_enabled_for("INFO")
    assert logger.is_enabled_for("ERROR")


@pytest.mark.parametrize("disabled_by", ["level", "logger", "global"])
def test_disabled_logging_skips_fields_and_record_creation(
    raw_logger: logging.Logger, monkeypatch: pytest.MonkeyPatch, disabled_by: str
) -> None:
    """Do not even read bound fields or attempt a stdlib record when disabled."""

    class UnreadableFields(Mapping[str, object]):
        def __iter__(self) -> Iterator[str]:
            raise AssertionError("disabled logging merged fields")

        def __len__(self) -> int:
            raise AssertionError("disabled logging inspected fields")

        def __getitem__(self, key: str) -> object:
            raise AssertionError("disabled logging read fields")

    logger = BoundLogger(raw_logger)
    monkeypatch.setattr(logger, "_context", UnreadableFields())
    make_record = Mock(side_effect=AssertionError("disabled logging created a record"))
    monkeypatch.setattr(raw_logger, "makeRecord", make_record)
    if disabled_by == "level":
        raw_logger.setLevel(logging.INFO)
    elif disabled_by == "logger":
        raw_logger.disabled = True
    else:
        logging.disable(logging.DEBUG)

    logger.debug("skipped", field="value")

    make_record.assert_not_called()


def test_enabled_check_does_not_inspect_handlers_or_filters(
    raw_logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eligibility does not promise that handlers or filters accept a record."""
    handler = logging.NullHandler(level=logging.CRITICAL)
    monkeypatch.setattr(raw_logger, "handlers", [handler])
    monkeypatch.setattr(raw_logger, "filters", [lambda record: False])
    assert BoundLogger(raw_logger).is_enabled_for("DEBUG")
