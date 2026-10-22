from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from cells.log.config import LogConfig
from cells.log.formatters import JsonLineFormatter
from cells.log.handlers import (
    ModuleFileRouterHandler,
    _sanitize_module_file_name,
    create_rotating_file_handler,
)


def _record(name: str, message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_module_router_uses_external_and_overflow_files(tmp_path: Path) -> None:
    config = LogConfig(
        log_dir=tmp_path,
        rotation="size",
        max_module_handlers=2,
        module_name_prefixes=("app",),
    ).normalized()
    router = ModuleFileRouterHandler(
        config,
        JsonLineFormatter(config),
        logging.INFO,
    )

    router.emit(_record("third.party", "external"))
    router.emit(_record("app.one", "one"))
    router.emit(_record("app.two", "overflow"))
    router.close()

    module_dir = tmp_path / "modules"
    assert "external" in (module_dir / "external.log").read_text(encoding="utf-8")
    assert "one" in (module_dir / "app_one.log").read_text(encoding="utf-8")
    assert "overflow" in (module_dir / "overflow.log").read_text(encoding="utf-8")


def test_size_rotation_creates_backup(tmp_path: Path) -> None:
    config = LogConfig(
        rotation="size",
        size_limit=1,
        retention=1,
    ).normalized()
    handler = create_rotating_file_handler(
        tmp_path / "app.log",
        config,
        JsonLineFormatter(config),
        logging.INFO,
    )

    handler.emit(_record("app", "first"))
    handler.emit(_record("app", "second"))
    handler.close()

    assert (tmp_path / "app.log").exists()
    assert (tmp_path / "app.log.1").exists()


def test_module_file_name_is_sanitized() -> None:
    assert _sanitize_module_file_name("../../ unsafe name") == "unsafe_name"


@pytest.mark.parametrize(
    "names",
    [
        ("audit.collision", "audit_collision"),
        ("audit.collision", "audit/collision"),
        pytest.param(
            ("audit.collision", "AUDIT.COLLISION"),
            marks=pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive paths"),
        ),
    ],
)
def test_module_filename_aliases_share_safe_rotation(
    tmp_path: Path,
    names: tuple[str, str],
) -> None:
    """Preserve all aliased module records across rotation and the handler cap."""
    config = LogConfig(
        log_dir=tmp_path,
        rotation="size",
        size_limit=1,
        retention=10,
        max_module_handlers=1,
    ).normalized()
    router = ModuleFileRouterHandler(config, JsonLineFormatter(config), logging.INFO)
    expected = []
    try:
        for index, name in enumerate(names * 2):
            message = f"record-{index}"
            router.handle(_record(name, message))
            expected.append((name, message))
    finally:
        router.close()

    records = [
        json.loads(line)
        for path in (tmp_path / "modules").glob("*")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert sorted((record["logger"], record["message"]) for record in records) == sorted(expected)
    assert not (tmp_path / "modules" / "overflow.log").exists()
