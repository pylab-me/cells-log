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


@pytest.mark.parametrize("has_log_dir", [False, True])
def test_bootstrap_ignores_production_file_and_queue_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    has_log_dir: bool,
) -> None:
    """Keep both implicit and explicit bootstrap console-only despite file flags."""
    log_dir = tmp_path / "must-not-be-created"
    for prefix in ("CELLS_LOG", "BOOT_TEST"):
        monkeypatch.setenv(f"{prefix}_ENABLE_MODULE_FILE", "true")
        monkeypatch.setenv(f"{prefix}_ENABLE_TOTAL_FILE", "true")
        monkeypatch.setenv(f"{prefix}_USE_QUEUE", "true")
        monkeypatch.setenv(f"{prefix}_FILE", str(log_dir / "total.log"))
        monkeypatch.setenv(f"{prefix}_LEVEL", "DEBUG")
        monkeypatch.setenv(f"{prefix}_ENABLE_CONSOLE", "true")
        if has_log_dir:
            monkeypatch.setenv(f"{prefix}_DIR", str(log_dir))
        else:
            monkeypatch.delenv(f"{prefix}_DIR", raising=False)

    manager = LogManager()
    try:
        manager.get_logger("early-import").debug("early-bootstrap")
        manager.bootstrap(app_name="boot", env_prefix="BOOT_TEST")
        manager.get_logger().debug("explicit-bootstrap")
        assert manager.stage is LogStage.BOOTSTRAP
        assert manager.get_queue_stats()["enabled"] is False
    finally:
        manager.shutdown()

    stderr = capsys.readouterr().err
    assert "early-bootstrap" in stderr
    assert "explicit-bootstrap" in stderr
    assert not log_dir.exists()
