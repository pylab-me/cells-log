from __future__ import annotations

from pathlib import Path

import pytest

from cells.log import LogConfig, configure_logging, get_logger
from cells.log.manager import LogManager, LogStage


def test_file_logging_requires_log_directory() -> None:
    with pytest.raises(ValueError, match="log_dir is required"):
        configure_logging(
            LogConfig(
                enable_console=False,
                enable_total_file=True,
                use_queue=False,
            )
        )


def test_force_false_preserves_first_production_configuration(
    tmp_path: Path,
) -> None:
    manager = LogManager()
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            total_file="first.log",
            use_queue=False,
        )
    )
    manager.configure(
        LogConfig(
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            total_file="second.log",
            use_queue=False,
        ),
        force=False,
    )
    manager.get_logger("demo").info("message")
    manager.shutdown()

    assert (tmp_path / "first.log").exists()
    assert not (tmp_path / "second.log").exists()


def test_manager_can_configure_after_shutdown() -> None:
    manager = LogManager()
    manager.shutdown()
    assert manager.stage is LogStage.SHUTDOWN

    manager.configure(LogConfig(enable_console=False, use_queue=False))

    assert manager.stage is LogStage.PRODUCTION


def test_logger_defaults_to_configured_application_name() -> None:
    configure_logging(
        LogConfig(
            app_name="service",
            enable_console=False,
            use_queue=False,
        )
    )

    assert get_logger().raw_logger.name == "service"
