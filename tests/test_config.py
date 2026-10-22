"""Configuration defaults, environment overrides and fallback diagnostics."""

from __future__ import annotations

import pytest

from cells.log.config import LogConfig, config_from_env
from cells.log.env import parse_size_limit


def test_queue_configuration_is_normalized() -> None:
    """Keep queue settings within the supported bounds."""
    config = LogConfig(
        queue_capacity=0,
        queue_full_policy="invalid",  # type: ignore[arg-type]
        queue_block_timeout=-1,
    ).normalized()

    assert config.queue_capacity == 1
    assert config.queue_full_policy == "drop"
    assert config.queue_block_timeout == 0


def test_queue_configuration_from_environment(monkeypatch) -> None:
    """Apply environment overrides to queue capacity and overflow policy."""
    monkeypatch.setenv("TEST_QUEUE_CAPACITY", "25")
    monkeypatch.setenv("TEST_QUEUE_FULL_POLICY", "sync_fallback")

    config = config_from_env(env_prefix="TEST")

    assert config.queue_capacity == 25
    assert config.queue_full_policy == "sync_fallback"


def test_root_logger_configuration_from_environment(monkeypatch) -> None:
    """Read the independent root and warnings integration switches."""
    monkeypatch.setenv("TEST_MANAGE_ROOT_LOGGER", "true")
    monkeypatch.setenv("TEST_CAPTURE_WARNINGS", "true")

    config = config_from_env(env_prefix="TEST")

    assert config.manage_root_logger is True
    assert config.capture_warnings is True


def test_parse_size_limit_supports_common_units() -> None:
    """Interpret file size units as binary multiples of bytes."""
    assert parse_size_limit("1KB") == 1024
    assert parse_size_limit("2MB") == 2 * 1024 * 1024
    assert parse_size_limit(12) == 12


@pytest.mark.parametrize(
    ("name", "invalid", "fallback", "allowed", "risk"),
    [
        (
            "queue_full_policy",
            "blok",
            "drop",
            "drop, block, sync_fallback",
            "records may be discarded when the queue is full",
        ),
        (
            "format",
            "jsno",
            "json",
            "json, text",
            "log output may not match the expected parser",
        ),
        (
            "rotation",
            "daliy",
            "size",
            "daily, size",
            "rotation may not follow the intended policy",
        ),
        (
            "retention",
            -1,
            0,
            "an integer >= 0",
            "disk usage may grow without the intended retention limit",
        ),
        (
            "max_module_handlers",
            0,
            1,
            "an integer >= 1",
            "more logger names may be routed to the overflow file",
        ),
        (
            "queue_capacity",
            0,
            1,
            "an integer >= 1",
            "the queue may fill more often and trigger its full policy",
        ),
        (
            "queue_block_timeout",
            -1.0,
            0.0,
            "a number >= 0 (seconds)",
            "queue insertion may time out immediately when full",
        ),
    ],
)
def test_invalid_configuration_emits_warning_and_keeps_fallback(
    capsys: pytest.CaptureFixture[str],
    name: str,
    invalid: object,
    fallback: object,
    allowed: str,
    risk: str,
) -> None:
    """Report invalid values, consequences and options while preserving fallbacks."""
    config = LogConfig(**{name: invalid}).normalized()

    assert getattr(config, name) == fallback
    assert capsys.readouterr().err == (
        f"cells-log: WARNING {name}={invalid!r} is invalid; "
        f"the effective value is {fallback!r}, so {risk}. "
        f"Valid values: {allowed}.\n"
    )
    config.normalized()
    assert capsys.readouterr().err == ""


def test_valid_configuration_does_not_emit_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Accept supported case normalization without diagnosing an error."""
    config = LogConfig(format="TEXT", rotation="DAILY").normalized()

    assert config.format == "text"
    assert config.rotation == "daily"
    assert capsys.readouterr().err == ""


def test_environment_policy_fallback_emits_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Expose the delivery consequence of an invalid environment policy."""
    monkeypatch.setenv("TEST_QUEUE_FULL_POLICY", "blok")

    assert config_from_env(env_prefix="TEST").queue_full_policy == "drop"
    assert capsys.readouterr().err == (
        "cells-log: WARNING queue_full_policy='blok' is invalid; "
        "the effective value is 'drop', so records may be discarded when the queue is full. "
        "Valid values: drop, block, sync_fallback.\n"
    )
