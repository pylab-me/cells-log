from __future__ import annotations

from cells.log.config import LogConfig, config_from_env
from cells.log.env import parse_size_limit


def test_queue_configuration_is_normalized() -> None:
    config = LogConfig(
        queue_capacity=0,
        queue_full_policy="invalid",  # type: ignore[arg-type]
        queue_block_timeout=-1,
    ).normalized()

    assert config.queue_capacity == 1
    assert config.queue_full_policy == "drop"
    assert config.queue_block_timeout == 0


def test_queue_configuration_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("TEST_QUEUE_CAPACITY", "25")
    monkeypatch.setenv("TEST_QUEUE_FULL_POLICY", "sync_fallback")

    config = config_from_env(env_prefix="TEST")

    assert config.queue_capacity == 25
    assert config.queue_full_policy == "sync_fallback"


def test_root_logger_configuration_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("TEST_MANAGE_ROOT_LOGGER", "true")
    monkeypatch.setenv("TEST_CAPTURE_WARNINGS", "true")

    config = config_from_env(env_prefix="TEST")

    assert config.manage_root_logger is True
    assert config.capture_warnings is True


def test_parse_size_limit_supports_common_units() -> None:
    assert parse_size_limit("1KB") == 1024
    assert parse_size_limit("2MB") == 2 * 1024 * 1024
    assert parse_size_limit(12) == 12
