"""v0.3.3 targeted API, integration and regression tests."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path

import pytest

from cells.log import (
    LogConfig,
    config_from_env,
    get_log_context,
    get_trace_id,
    log_context,
)
from cells.log.manager import LogManager


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _state(logger: logging.Logger) -> tuple:
    return (tuple(logger.handlers), logger.level, logger.propagate, logger.disabled)


def _cfg(tmp_path: Path, **kwargs: object) -> LogConfig:
    from dataclasses import replace

    return replace(
        LogConfig(
            app_name="sample",
            enable_console=False,
            enable_total_file=True,
            log_dir=tmp_path,
            use_queue=False,
        ),
        **kwargs,
    )


def test_context_scope_restores_nested_and_exception() -> None:
    initial_trace = get_trace_id()
    initial_fields = get_log_context()
    with log_context(trace_id="outer", request_id="r1", stage="outer"):
        assert get_trace_id() == "outer"
        assert get_log_context()["request_id"] == "r1"
        with pytest.raises(RuntimeError, match="expected"):
            with log_context(trace_id="inner", stage="inner", job_id=42):
                assert get_log_context()["stage"] == "inner"
                assert get_log_context()["job_id"] == 42
                assert get_trace_id() == "inner"
                raise RuntimeError("expected")
        assert get_trace_id() == "outer"
        assert get_log_context()["stage"] == "outer"
        assert "job_id" not in get_log_context()
    assert get_trace_id() == initial_trace
    assert get_log_context() == initial_fields


def test_context_scope_preserves_current_trace_when_unspecified() -> None:
    with log_context(trace_id="keep"):
        with log_context(user_id=1):
            assert get_trace_id() == "keep"
        assert get_trace_id() == "keep"


def test_context_scope_isolates_async_tasks_and_threads() -> None:
    async def worker(trace: str) -> tuple[str, str]:
        with log_context(trace_id=trace, request_id=trace):
            await asyncio.sleep(0)
            return get_trace_id(), get_log_context()["request_id"]

    async def both():
        return await asyncio.gather(worker("a"), worker("b"))

    assert asyncio.run(both()) == [("a", "a"), ("b", "b")]
    with log_context(trace_id="main", request_id="main"):
        result: list[tuple[str, dict]] = []

        def target() -> None:
            result.append((get_trace_id(), get_log_context()))

        t = threading.Thread(target=target)
        t.start()
        t.join(timeout=3)
        assert not t.is_alive()
        assert result[0][0] != "main"
        assert result[0][1].get("request_id") != "main"


def test_format_for_console_text_file_json(tmp_path: Path, capsys) -> None:
    mgr = LogManager()
    mgr.configure(
        _cfg(
            tmp_path,
            enable_console=True,
            console_to_stderr=True,
            format_for_console="text",
            format_for_file="json",
        )
    )
    try:
        mgr.get_logger("format.mixed").info("mixed-sink", item_id=123)
    finally:
        mgr.shutdown()
    console = capsys.readouterr().err
    assert "mixed-sink" in console and "item_id" in console
    assert not any(line.startswith("{") for line in console.splitlines())
    assert _records(tmp_path / "sample.log")[0]["fields"]["item_id"] == 123


def test_format_for_console_json_file_text(tmp_path: Path, capsys) -> None:
    mgr = LogManager()
    mgr.configure(
        _cfg(
            tmp_path,
            enable_console=True,
            format_for_console="JSON",
            format_for_file="TEXT",
        )
    )
    try:
        mgr.get_logger("format.inverse").warning("inverse-sink", event_id=12)
    finally:
        mgr.shutdown()
    assert json.loads(capsys.readouterr().err.splitlines()[-1])["message"] == "inverse-sink"
    output = (tmp_path / "sample.log").read_text()
    assert "inverse-sink" in output and not output.startswith("{")


def test_single_processor_invocation_with_mixed_formats(tmp_path: Path) -> None:
    called: list[dict] = []

    def process(fields: dict) -> dict:
        called.append(fields)
        return fields

    mgr = LogManager()
    mgr.configure(
        _cfg(
            tmp_path,
            enable_console=True,
            format_for_console="text",
            format_for_file="json",
            field_processor=process,
        )
    )
    try:
        mgr.get_logger("format.processor").info("processed once", key=1)
    finally:
        mgr.shutdown()
    assert len(called) == 1


def test_selective_capture_and_shutdown_restore_original(tmp_path: Path) -> None:
    third = logging.getLogger("unique.thirdparty.v033")
    before = _state(third)
    original_handler = logging.NullHandler()
    third.handlers[:] = [original_handler]
    third.setLevel(logging.ERROR)
    third.propagate = True
    middle = _state(third)
    mgr = LogManager()
    try:
        mgr.configure(_cfg(tmp_path, capture_loggers=(third.name,)))
        third.warning("thirdparty_event", extra={"duration_ms": 45})
        logging.getLogger("unique.thirdparty.other").warning("not captured")
    finally:
        mgr.shutdown()
    assert _state(third) == middle
    assert [r["message"] for r in _records(tmp_path / "sample.log")] == ["thirdparty_event"]
    assert _records(tmp_path / "sample.log")[0]["fields"]["duration_ms"] == 45
    assert original_handler not in mgr._handlers
    # Explicitly return test-created external logger to its initial state.
    third.handlers[:], third.level, third.propagate, third.disabled = before


def test_selective_capture_reconfigure_and_release(tmp_path: Path) -> None:
    a = logging.getLogger("capture.one.v033")
    b = logging.getLogger("capture.two.v033")
    initial_a, initial_b = _state(a), _state(b)
    mgr = LogManager()
    try:
        mgr.configure(_cfg(tmp_path, capture_loggers=(a.name,)))
        a.info("a-one")
        mgr.configure(_cfg(tmp_path, capture_loggers=(b.name,)))
        assert _state(a) == initial_a
        b.warning("b-two")
    finally:
        mgr.shutdown()
    assert _state(b) == initial_b
    assert [r["message"] for r in _records(tmp_path / "sample.log")] == ["a-one", "b-two"]


def test_selective_capture_with_root_has_no_duplicate(tmp_path: Path) -> None:
    special = logging.getLogger("captured.with.root.v033")
    before = _state(special)
    mgr = LogManager()
    try:
        mgr.configure(
            _cfg(
                tmp_path,
                manage_root_logger=True,
                capture_loggers=(special.name,),
            )
        )
        special.info("only-once")
        assert [x["message"] for x in _records(tmp_path / "sample.log")] == ["only-once"]
    finally:
        mgr.shutdown()
    assert _state(special) == before


def test_capture_collision_is_rejected_without_mutation(tmp_path: Path) -> None:
    mgr = LogManager()
    try:
        mgr.get_logger("owned.v033")
        with pytest.raises(ValueError, match="conflicts"):
            mgr.configure(_cfg(tmp_path, capture_loggers=("owned.v033",)))
        mgr.configure(_cfg(tmp_path, capture_loggers=("captured.v033",)))
        with pytest.raises(ValueError, match="conflicts"):
            mgr.get_logger("captured.v033")
    finally:
        mgr.shutdown()


def test_activation_failure_restores_captured_external(tmp_path: Path, monkeypatch) -> None:
    special = logging.getLogger("rollback.external.v033")
    before = _state(special)
    mgr = LogManager()
    mgr.configure(_cfg(tmp_path))

    def fail_sync(*args, **kwargs):
        raise RuntimeError("forced activation error")

    mgr.get_logger("some.owned")
    monkeypatch.setattr(mgr, "_sync_logger", fail_sync)
    try:
        with pytest.raises(RuntimeError, match="forced"):
            mgr.configure(_cfg(tmp_path, capture_loggers=(special.name,)))
        assert _state(special) == before
        assert mgr._captured_logger_states == {}
    finally:
        mgr.shutdown()


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "bad"),
        ("format_for_console", "ansi"),
        ("format_for_file", "xml"),
        ("queue_full_policy", "bad"),
        ("rotation", "weekly"),
        ("queue_capacity", 0),
        ("queue_block_timeout", "bad"),
        ("level", "IMPOSSIBLE"),
        ("size_limit", "too much"),
    ],
)
def test_strict_config_rejects_invalid_values(tmp_path: Path, field: str, value) -> None:
    cfg = _cfg(tmp_path)
    setattr(cfg, field, value)
    with pytest.raises(ValueError):
        cfg.validate()
    mgr = LogManager()
    try:
        with pytest.raises(ValueError):
            mgr.configure(cfg, strict=True)
    finally:
        mgr.shutdown()


def test_strict_config_does_not_reconfigure_on_failure(tmp_path: Path) -> None:
    mgr = LogManager()
    mgr.configure(_cfg(tmp_path))
    old = mgr._config
    try:
        with pytest.raises(ValueError, match="queue_full_policy"):
            mgr.configure(_cfg(tmp_path, queue_full_policy="unknown"), strict=True)
        assert mgr._config is old
        mgr.get_logger("service").info("retained")
    finally:
        mgr.shutdown()
    assert _records(tmp_path / "sample.log")[0]["message"] == "retained"


@pytest.mark.parametrize("names", [("root",), ("py.warnings",), ("same", "same"), ("",)])
def test_invalid_capture_names_rejected(tmp_path: Path, names: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        _cfg(tmp_path, capture_loggers=names).normalized()


def test_config_from_env_preserves_base_name_and_format_overrides(
    tmp_path: Path, monkeypatch
) -> None:
    base = _cfg(tmp_path, app_name="billing", format_for_console="text")
    cfg = config_from_env(base=base, env_prefix="V033")
    assert cfg.app_name == "billing"
    assert cfg.format_for_console == "text"
    monkeypatch.setenv("V033_FORMAT_FOR_FILE", "text")
    monkeypatch.setenv("V033_CAPTURE_LOGGERS", "uvicorn.error, sqlalchemy.engine")
    cfg = config_from_env(base=base, env_prefix="V033", app_name="override")
    assert cfg.app_name == "override"
    assert cfg.format_for_file == "text"
    assert cfg.capture_loggers == ("uvicorn.error", "sqlalchemy.engine")


def test_selective_capture_with_warnings_and_queue(tmp_path: Path) -> None:
    import warnings

    third = logging.getLogger("selective.with.warnings.v033")
    before = _state(third)
    mgr = LogManager()
    try:
        mgr.configure(
            _cfg(
                tmp_path,
                capture_loggers=(third.name,),
                capture_warnings=True,
                use_queue=True,
                queue_capacity=128,
            )
        )
        third.warning("third-warn", extra={"run_id": 1})
        warnings.warn("python-warn", UserWarning, stacklevel=1)
        assert mgr.flush(timeout=3)
    finally:
        mgr.shutdown()
    assert _state(third) == before
    recs = _records(tmp_path / "sample.log")
    assert sum(rec["message"] == "third-warn" for rec in recs) == 1
    assert any("python-warn" in rec["message"] for rec in recs)


def test_version_example_executes(tmp_path: Path, monkeypatch) -> None:
    import inspect
    import textwrap
    from cells.log import version

    monkeypatch.chdir(tmp_path)
    doc = inspect.getdoc(version)
    assert doc is not None
    script = doc.split("standalone script::\n", 1)[1].split("\nTrace and bound", 1)[0]
    exec(compile(textwrap.dedent(script), "version.py example", "exec"), {"__name__": "__main__"})
    assert (tmp_path / "logs" / "example.log").exists()


def test_strict_none_format_and_level_rejected() -> None:
    with pytest.raises(ValueError, match="format"):
        LogConfig(format=None).validate()  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="level"):
        LogConfig(level=None).validate()  # type: ignore[arg-type]


def test_capture_loggers_accepts_list_but_normalizes_to_tuple() -> None:
    cfg = LogConfig(capture_loggers=["log.a", "log.b"]).normalized()
    assert cfg.capture_loggers == ("log.a", "log.b")
    cfg.validate()


def test_fork_child_restores_external_capture(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys
    import textwrap

    if not hasattr(os, "fork"):
        pytest.skip("requires POSIX fork")

    # Exercise fork in a clean interpreter, not pytest's multithreaded process.
    program = textwrap.dedent(
        """\
        import os, logging, sys
        from cells.log import LogConfig
        from cells.log.manager import LogManager
        logger = logging.getLogger("fork.capture.standalone")
        before = (list(logger.handlers), logger.level, logger.disabled, logger.propagate)
        mgr = LogManager()
        mgr.configure(LogConfig(
            enable_console=False, capture_loggers=(logger.name,), use_queue=False
        ))
        pid = os.fork()
        if pid == 0:
            restored = (list(logger.handlers), logger.level, logger.disabled, logger.propagate)
            os._exit(0 if restored == before else 7)
        _, status = os.waitpid(pid, 0)
        mgr.shutdown()
        sys.exit(0 if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0 else 8)
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
