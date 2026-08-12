from __future__ import annotations

from pathlib import Path

import pytest

from cells.log.env import env_bool, env_int, env_key, env_path, parse_size_limit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("NO", False),
        ("off", False),
    ],
)
def test_env_bool_recognizes_common_values(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("BOOL_VALUE", raw)

    assert env_bool("BOOL_VALUE", not expected) is expected


def test_env_bool_uses_default_for_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOOL_VALUE", "invalid")

    assert env_bool("BOOL_VALUE", True) is True


def test_env_int_parses_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INT_VALUE", " 42 ")
    assert env_int("INT_VALUE", 1) == 42

    monkeypatch.setenv("INT_VALUE", "invalid")
    assert env_int("INT_VALUE", 1) == 1


def test_env_path_expands_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH_VALUE", "~/logs")

    assert env_path("PATH_VALUE") == Path("~/logs").expanduser()


def test_env_key_normalizes_case() -> None:
    assert env_key("cells_log", "queue_capacity") == "CELLS_LOG_QUEUE_CAPACITY"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2GB", 2 * 1024 * 1024 * 1024),
        ("3MB", 3 * 1024 * 1024),
        ("4KB", 4 * 1024),
        ("5B", 5),
        ("6", 6),
        ("invalid", 100 * 1024 * 1024),
    ],
)
def test_parse_size_limit(
    raw: str,
    expected: int,
) -> None:
    assert parse_size_limit(raw) == expected
