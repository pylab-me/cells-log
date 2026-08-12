from __future__ import annotations

import pytest

from cells.log import LogConfig, configure_logging, shutdown_logging


@pytest.fixture(autouse=True)
def reset_logging() -> None:
    """Keep the module-level logging manager isolated between tests."""
    configure_logging(LogConfig(enable_console=False, use_queue=False))
    yield
    shutdown_logging()
