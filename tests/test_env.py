"""Environment value parsing and visible fallback diagnostics."""

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
    """Recognize supported boolean spellings without using the default."""
    monkeypatch.setenv("BOOL_VALUE", raw)

    assert env_bool("BOOL_VALUE", not expected) is expected


def test_env_bool_uses_default_for_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve the caller's boolean default for an unrecognized value."""
    monkeypatch.setenv("BOOL_VALUE", "invalid")

    assert env_bool("BOOL_VALUE", True) is True


def test_env_int_parses_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse surrounding whitespace and retain the default on invalid integers."""
    monkeypatch.setenv("INT_VALUE", " 42 ")
    assert env_int("INT_VALUE", 1) == 42

    monkeypatch.setenv("INT_VALUE", "invalid")
    assert env_int("INT_VALUE", 1) == 1


def test_env_path_expands_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expand home-relative paths without creating directories."""
    monkeypatch.setenv("PATH_VALUE", "~/logs")

    assert env_path("PATH_VALUE") == Path("~/logs").expanduser()


def test_env_key_normalizes_case() -> None:
    """Generate consistently uppercase environment variable names."""
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
    """Parse supported sizes and preserve the documented invalid-size fallback."""
    assert parse_size_limit(raw) == expected


@pytest.mark.parametrize(
    ("parser", "fallback", "allowed", "risk"),
    [
        (
            env_bool,
            True,
            "true: 1, true, yes, y, on; false: 0, false, no, n, off (case-insensitive)",
            "the feature state may differ from the intended setting",
        ),
        (
            env_int,
            12,
            "an integer",
            "the numeric setting may differ from the intended limit",
        ),
    ],
)
def test_invalid_environment_values_emit_warnings(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    parser,
    fallback: bool | int,
    allowed: str,
    risk: str,
) -> None:
    """Include consequences and accepted inputs in environment diagnostics."""
    monkeypatch.setenv("TEST_INVALID", "invalid")

    assert parser("TEST_INVALID", fallback) == fallback
    assert capsys.readouterr().err == (
        "cells-log: WARNING TEST_INVALID='invalid' is invalid; "
        f"the effective value is {fallback!r}, so {risk}. "
        f"Valid values: {allowed}.\n"
    )


@pytest.mark.parametrize("raw", ["invalid", "invalidMB"])
def test_invalid_size_limit_emits_warning(capsys: pytest.CaptureFixture[str], raw: str) -> None:
    """Explain the actual size fallback and the supported unit syntax."""
    assert parse_size_limit(raw) == 100 * 1024 * 1024
    assert capsys.readouterr().err == (
        f"cells-log: WARNING size_limit={raw!r} is invalid; "
        "the effective value is '100MB', so files may rotate at an unintended size threshold. "
        "Valid values: an integer byte count or an integer followed by B, KB, MB, GB.\n"
    )
