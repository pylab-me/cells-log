from __future__ import annotations

import json
import logging
import warnings
from pathlib import Path

from cells.log import LogConfig
from cells.log.manager import LogManager


def _messages(path: Path) -> list[str]:
    return [
        json.loads(line)["message"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_capture_warnings_routes_to_cells_sink_without_root_takeover(
    tmp_path: Path,
) -> None:
    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            capture_warnings=True,
            use_queue=False,
        )
    )

    warnings.warn("captured-warning", UserWarning, stacklevel=1)
    manager.shutdown()

    assert any("captured-warning" in message for message in _messages(tmp_path / "app.log"))


def test_root_takeover_captures_third_party_logger_once(tmp_path: Path) -> None:
    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            manage_root_logger=True,
            use_queue=False,
        )
    )

    logging.getLogger("third.party").warning("third-party")
    manager.shutdown()

    assert _messages(tmp_path / "app.log") == ["third-party"]


def test_managed_cells_logger_does_not_duplicate_through_root(
    tmp_path: Path,
) -> None:
    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            manage_root_logger=True,
            use_queue=False,
        )
    )

    manager.get_logger("service").info("owned")
    manager.shutdown()

    assert _messages(tmp_path / "app.log") == ["owned"]


def test_default_configuration_does_not_take_over_root(tmp_path: Path) -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            use_queue=False,
        )
    )

    assert root_logger.handlers == original_handlers
    assert root_logger.level == original_level
    manager.shutdown()


def test_shutdown_restores_root_and_warnings_logger_state(tmp_path: Path) -> None:
    root_logger = logging.getLogger()
    warnings_logger = logging.getLogger("py.warnings")
    root_state = (
        list(root_logger.handlers),
        root_logger.level,
        root_logger.propagate,
        root_logger.disabled,
    )
    warnings_state = (
        list(warnings_logger.handlers),
        warnings_logger.level,
        warnings_logger.propagate,
        warnings_logger.disabled,
    )
    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            manage_root_logger=True,
            capture_warnings=True,
            use_queue=False,
        )
    )
    manager.shutdown()

    assert (
        root_logger.handlers,
        root_logger.level,
        root_logger.propagate,
        root_logger.disabled,
    ) == root_state
    assert (
        warnings_logger.handlers,
        warnings_logger.level,
        warnings_logger.propagate,
        warnings_logger.disabled,
    ) == warnings_state


def test_reconfigure_restores_external_loggers_before_disabling_takeover(
    tmp_path: Path,
) -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    manager = LogManager()
    manager.configure(
        LogConfig(
            enable_console=False,
            manage_root_logger=True,
            capture_warnings=True,
            use_queue=False,
        )
    )
    manager.configure(LogConfig(enable_console=False, use_queue=False))

    assert root_logger.handlers == original_handlers
    assert logging.getLogger("py.warnings").propagate is True
    manager.shutdown()
