"""Failure and concurrency guarantees for logging graph replacement."""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread

import pytest

import cells.log.manager as manager_module
from cells.log.config import LogConfig
from cells.log.context import (
    get_default_trace_id,
    get_trace_id,
    reset_trace_id,
    set_trace_id,
)
from cells.log.handlers import ModuleFileRouterHandler, SafeQueueListener
from cells.log.manager import LogManager, LogStage


def _config(tmp_path: Path, name: str, **options: object) -> LogConfig:
    return replace(
        LogConfig(
            app_name=name,
            level="DEBUG",
            log_dir=tmp_path,
            enable_console=False,
            enable_total_file=True,
            total_file=f"{name}.log",
            default_trace_id=name,
            use_queue=False,
        ),
        **options,
    )


def _records(tmp_path: Path, name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (tmp_path / f"{name}.log").read_text(encoding="utf-8").splitlines()
    ]


def _logger_state(logger: logging.Logger) -> tuple:
    return tuple(logger.handlers), logger.level, logger.propagate, logger.disabled


@pytest.mark.parametrize("use_queue", [False, True])
def test_partial_preparation_closes_candidates_and_keeps_old_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_queue: bool
) -> None:
    """Discard partial candidates while old records and fallback remain usable."""
    manager = LogManager()
    manager.configure(_config(tmp_path, "old", use_queue=use_queue))
    logger = manager.get_logger("transaction.partial")
    previous_config = manager._config
    previous_listener = manager._queue_listener
    previous_handlers = tuple(manager._handlers)
    candidates = []
    router_type = ModuleFileRouterHandler

    class _TrackedRouter(router_type):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            candidates.append(self)

    monkeypatch.setattr(manager_module, "ModuleFileRouterHandler", _TrackedRouter)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block", encoding="utf-8")
    try:
        logger.debug("before")
        with pytest.raises(OSError):
            manager.configure(
                _config(
                    tmp_path,
                    "candidate",
                    level="ERROR",
                    enable_module_file=True,
                    total_file=blocker / "candidate.log",
                )
            )
        assert manager.stage is LogStage.PRODUCTION
        assert manager._config is previous_config
        assert manager._queue_listener is previous_listener
        assert all(not handler._closed for handler in previous_handlers)
        assert len(candidates) == 1 and candidates[0]._closed
        assert get_default_trace_id() == "old"
        logger.debug("after")
    finally:
        manager.shutdown()

    records = _records(tmp_path, "old")
    assert [record["message"] for record in records] == ["before", "after"]
    assert {record["trace_id"] for record in records} == {"old"}


@pytest.mark.parametrize("failure", ["before", "after", "thread", "interrupt"])
def test_listener_start_failure_releases_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Clean up both unstarted and running candidate listeners after failure."""
    manager = LogManager()
    manager.configure(_config(tmp_path, "old", use_queue=True))
    logger = manager.get_logger("transaction.listener")
    previous_listener = manager._queue_listener
    candidates = []
    listener_type = SafeQueueListener
    start = listener_type.start

    class _FailingListener(listener_type):
        def start(self):
            candidates.append(self)
            if failure == "after":
                start(self)
            if failure == "thread":
                with monkeypatch.context() as patch:

                    def fail_thread_start(thread):
                        raise RuntimeError("thread startup failed")

                    patch.setattr(Thread, "start", fail_thread_start)
                    start(self)
            if failure == "interrupt":
                raise KeyboardInterrupt("candidate interrupted")
            raise RuntimeError("candidate startup failed")

    monkeypatch.setattr(manager_module, "SafeQueueListener", _FailingListener)
    expected = KeyboardInterrupt if failure == "interrupt" else RuntimeError
    try:
        with pytest.raises(expected):
            manager.configure(_config(tmp_path, "candidate", use_queue=True, level="ERROR"))
        assert manager._queue_listener is previous_listener
        assert previous_listener._thread.is_alive()
        assert get_default_trace_id() == "old"
        assert len(candidates) == 1
        assert candidates[0]._thread is None
        assert all(handler._closed for handler in candidates[0].handlers)
        logger.debug("still-active")
    finally:
        manager.shutdown()
    assert [record["message"] for record in _records(tmp_path, "old")] == ["still-active"]


@pytest.mark.parametrize("previous_external", [False, True])
def test_activation_failure_restores_loggers_and_warning_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, previous_external: bool
) -> None:
    """Restore both application and external logger state after partial activation."""
    manager = LogManager()
    originals = {name: _logger_state(logging.getLogger(name)) for name in ("", "py.warnings")}
    original_showwarning = warnings.showwarning
    manager.configure(
        _config(
            tmp_path,
            "old",
            use_queue=True,
            manage_root_logger=previous_external,
            capture_warnings=previous_external,
        )
    )
    first = manager.get_logger("transaction.activation.first")
    second = manager.get_logger("transaction.activation.second")
    loggers = [logging.getLogger(), logging.getLogger("py.warnings")]
    loggers.extend([first.raw_logger, second.raw_logger])
    snapshots = [_logger_state(logger) for logger in loggers]
    showwarning = warnings.showwarning
    capture_state = logging._warnings_showwarning
    ownership = manager._root_state, manager._warnings_state
    previous_config = manager._config
    sync_logger = manager._sync_logger
    prepare_graph = manager._prepare_graph
    candidates = []
    calls = 0

    def track_preparation(config):
        graph = prepare_graph(config)
        candidates.append((graph.listener, tuple(graph.handlers)))
        return graph

    def fail_sync(raw_logger, **kwargs):
        nonlocal calls
        sync_logger(raw_logger, **kwargs)
        calls += 1
        if calls == 2:
            raise RuntimeError("partial logger activation")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(manager, "_sync_logger", fail_sync)
            patch.setattr(manager, "_prepare_graph", track_preparation)
            with pytest.raises(RuntimeError, match="partial logger activation"):
                manager.configure(
                    _config(
                        tmp_path,
                        "candidate",
                        level="ERROR",
                        use_queue=True,
                        manage_root_logger=not previous_external,
                        capture_warnings=not previous_external,
                    )
                )
        assert manager._config is previous_config
        assert manager.stage is LogStage.PRODUCTION
        assert [_logger_state(logger) for logger in loggers] == snapshots
        assert warnings.showwarning is showwarning
        assert logging._warnings_showwarning is capture_state
        assert (manager._root_state, manager._warnings_state) == ownership
        assert get_default_trace_id() == "old"
        assert len(candidates) == 1
        listener, handlers = candidates[0]
        assert listener._thread is None
        assert all(handler._closed for handler in handlers)
        first.debug("first-after")
        second.debug("second-after")
    finally:
        manager.shutdown()

    assert [record["message"] for record in _records(tmp_path, "old")] == [
        "first-after",
        "second-after",
    ]
    assert {name: _logger_state(logging.getLogger(name)) for name in originals} == originals
    assert warnings.showwarning is original_showwarning


@pytest.mark.parametrize("failure", ["routing", "trace"])
def test_failure_after_mutation_restores_route_and_process_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Undo a late failure even with an explicit caller trace context."""
    manager = LogManager()
    manager.configure(_config(tmp_path, "old"))
    logger = manager.get_logger("transaction.late")
    previous_config = manager._config
    calls = 0
    token = set_trace_id("request-trace")
    if failure == "routing":
        target = manager._switching_handler
        attribute = "replace_handlers"
    else:
        target = manager_module
        attribute = "set_default_trace_id"
    original = getattr(target, attribute)

    def fail_once(value):
        nonlocal calls
        original(value)
        calls += 1
        if calls == 1:
            raise RuntimeError("late activation failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(target, attribute, fail_once)
            with pytest.raises(RuntimeError, match="late activation failure"):
                manager.configure(_config(tmp_path, "candidate", level="ERROR"))
        assert manager._config is previous_config
        assert manager.stage is LogStage.PRODUCTION
        assert get_default_trace_id() == "old"
        assert get_trace_id() == "request-trace"
        logger.debug("request-after")
        reset_trace_id(token)
        token = None
        logger.debug("fallback-after")
    finally:
        if token is not None:
            reset_trace_id(token)
        manager.shutdown()

    records = _records(tmp_path, "old")
    assert [record["message"] for record in records] == ["request-after", "fallback-after"]
    assert [record["trace_id"] for record in records] == ["request-trace", "old"]


@pytest.mark.parametrize("resource", ["handler", "listener"])
def test_cleanup_failure_keeps_new_graph_and_retries_on_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    resource: str,
) -> None:
    """Keep a committed replacement when old resource cleanup needs a retry."""
    manager = LogManager()
    manager.configure(_config(tmp_path, "old", use_queue=resource == "listener"))
    logger = manager.get_logger("transaction.cleanup")
    old_handler = manager._handlers[0]
    target = manager._queue_listener if resource == "listener" else old_handler
    attribute = "stop" if resource == "listener" else "close"
    dispose = getattr(target, attribute)
    calls = 0

    def fail_cleanup_once():
        nonlocal calls
        calls += 1
        assert manager._config.app_name == "new"
        if calls == 1:
            raise OSError("old handle still busy")
        dispose()

    monkeypatch.setattr(target, attribute, fail_cleanup_once)
    try:
        manager.configure(_config(tmp_path, "new", use_queue=True))
        assert manager.stage is LogStage.PRODUCTION
        assert get_default_trace_id() == "new"
        assert not old_handler._closed
        operation = "listener cleanup" if resource == "listener" else "handler close"
        assert f"{operation} failed: OSError: old handle still busy" in capsys.readouterr().err
        logger.debug("new-active")
    finally:
        manager.shutdown()
    assert calls == 2 and old_handler._closed
    assert not manager._retired_graphs
    assert [record["message"] for record in _records(tmp_path, "new")] == ["new-active"]


def test_cleanup_error_preserves_original_preparation_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report cleanup trouble without replacing the configuration failure."""
    manager = LogManager()
    manager.configure(_config(tmp_path, "old"))
    logger = manager.get_logger("transaction.cleanup-candidate")
    candidates = []

    class _FailingRouter(ModuleFileRouterHandler):
        def close(self):
            if not candidates:
                candidates.append(self)
                raise OSError("candidate close failed")
            super().close()

    monkeypatch.setattr(manager_module, "ModuleFileRouterHandler", _FailingRouter)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block", encoding="utf-8")
    try:
        with pytest.raises(OSError) as error:
            manager.configure(
                _config(
                    tmp_path,
                    "candidate",
                    enable_module_file=True,
                    total_file=blocker / "candidate.log",
                )
            )
        assert str(error.value) != "candidate close failed"
        assert "candidate close failed" in capsys.readouterr().err
        assert manager._config.app_name == "old"
        assert get_default_trace_id() == "old"
        assert len(candidates) == 1 and not candidates[0]._closed
        logger.debug("old-still-active")
    finally:
        manager.shutdown()
    assert candidates[0]._closed
    assert not manager._retired_graphs
    assert [record["message"] for record in _records(tmp_path, "old")] == ["old-still-active"]


@pytest.mark.parametrize("preparation_succeeds", [False, True])
@pytest.mark.parametrize("old_queue", [False, True])
@pytest.mark.parametrize("pause_at", ["build", "listener"])
@pytest.mark.parametrize("manage_root", [False, True])
def test_old_graph_serves_while_candidate_is_preparing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preparation_succeeds: bool,
    old_queue: bool,
    pause_at: str,
    manage_root: bool,
) -> None:
    """Complete old writes and runtime reads before candidate preparation resumes."""
    manager = LogManager()
    manager.configure(
        _config(
            tmp_path,
            "old",
            use_queue=old_queue,
            manage_root_logger=manage_root,
            capture_warnings=True,
        )
    )
    logger = manager.get_logger("transaction.concurrent")
    preparing = Event()
    release = Event()
    produced = Event()
    failures = []

    def pause_preparation():
        preparing.set()
        assert release.wait(5), "candidate release timed out"
        if not preparation_succeeds:
            raise RuntimeError("candidate failed")

    build = manager._build_handlers

    def paused_build(*args, **kwargs):
        build(*args, **kwargs)
        if pause_at == "build":
            pause_preparation()

    class _PausedListener(SafeQueueListener):
        def start(self):
            super().start()
            if pause_at == "listener":
                pause_preparation()

    def configure():
        try:
            manager.configure(
                _config(tmp_path, "candidate", use_queue=True, manage_root_logger=manage_root)
            )
        except BaseException as exc:
            failures.append(exc)

    def produce():
        try:
            assert manager.get_queue_stats()["enabled"] is old_queue
            logger.debug("retained-before-ready")
            manager.get_logger("transaction.created-during-prepare").debug(
                "new-logger-before-ready"
            )
            logging.getLogger("py.warnings").warning("warning-before-ready")
            if manage_root:
                logging.getLogger("third-party").debug("root-before-ready")
            manager._drain_queue()
            produced.set()
        except BaseException as exc:
            failures.append(exc)

    monkeypatch.setattr(manager_module, "SafeQueueListener", _PausedListener)
    monkeypatch.setattr(manager, "_build_handlers", paused_build)
    reloader = Thread(target=configure, daemon=True)
    producer = Thread(target=produce, daemon=True)
    expected = ["retained-before-ready", "new-logger-before-ready", "warning-before-ready"]
    if manage_root:
        expected.append("root-before-ready")
    try:
        reloader.start()
        assert preparing.wait(5)
        assert manager.stage is LogStage.PRODUCTION
        assert manager._config.app_name == "old"
        assert get_default_trace_id() == "old"
        producer.start()
        assert produced.wait(2), "old logging blocked on candidate preparation"
        before = _records(tmp_path, "old")
        assert [record["message"] for record in before] == expected
        assert {record["trace_id"] for record in before} == {"old"}
        release.set()
        reloader.join(5)
        producer.join(5)
        assert not reloader.is_alive() and not producer.is_alive()
        if preparation_succeeds:
            assert not failures
        else:
            assert len(failures) == 1 and str(failures[0]) == "candidate failed"
        active = "candidate" if preparation_succeeds else "old"
        assert manager._config.app_name == active
        logger.debug("after-preparation")
    finally:
        release.set()
        reloader.join(5)
        if producer.ident is not None:
            producer.join(5)
        manager.shutdown()
    records = _records(tmp_path, active)
    assert records[-1]["message"] == "after-preparation"
    assert records[-1]["trace_id"] == active
    all_records = _records(tmp_path, "old")
    if preparation_succeeds:
        all_records += _records(tmp_path, "candidate")
    assert [record["message"] for record in all_records] == [*expected, "after-preparation"]


@pytest.mark.parametrize("module_file", [False, True])
@pytest.mark.parametrize("old_queue", [False, True])
def test_same_file_rotates_during_preparation_and_retired_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    module_file: bool,
    old_queue: bool,
) -> None:
    """Keep old and new file writers usable while preparation or cleanup pauses."""
    manager = LogManager()
    config = _config(
        tmp_path,
        "shared",
        use_queue=old_queue,
        rotation="size",
        size_limit=1,
        retention=20,
        enable_module_file=module_file,
        enable_total_file=not module_file,
    )
    manager.configure(config)
    logger = manager.get_logger("transaction.same-file")
    logger.info("initial")
    manager._drain_queue()
    preparing, release_prepare = Event(), Event()
    cleaning, release_cleanup = Event(), Event()
    produced = Event()
    failures = []
    old_handler = manager._handlers[0]
    target = manager._queue_listener if old_queue else old_handler
    attribute = "stop" if old_queue else "close"
    cleanup = getattr(target, attribute)

    class _PausedListener(SafeQueueListener):
        def start(self):
            super().start()
            preparing.set()
            assert release_prepare.wait(5)

    def paused_cleanup():
        cleaning.set()
        assert release_cleanup.wait(5)
        cleanup()

    def configure():
        try:
            manager.configure(replace(config, use_queue=True, default_trace_id="new"))
        except BaseException as exc:
            failures.append(exc)

    def produce(prefix):
        try:
            manager.get_logger("transaction.same-file")
            for number in range(3):
                logger.info(f"{prefix}-{number}")
            manager._drain_queue()
            produced.set()
        except BaseException as exc:
            failures.append(exc)

    monkeypatch.setattr(manager_module, "SafeQueueListener", _PausedListener)
    monkeypatch.setattr(target, attribute, paused_cleanup)
    reloader = Thread(target=configure, daemon=True)
    producers = []
    try:
        reloader.start()
        assert preparing.wait(5)
        for prefix, ready, release in (
            ("old", preparing, release_prepare),
            ("new", cleaning, release_cleanup),
        ):
            assert ready.wait(5)
            produced.clear()
            producer = Thread(target=produce, args=(prefix,), daemon=True)
            producers.append(producer)
            producer.start()
            assert produced.wait(2), f"logging blocked during {prefix} phase"
            producer.join(5)
            release.set()
        reloader.join(5)
        assert not reloader.is_alive()
        assert not failures
    finally:
        release_prepare.set()
        release_cleanup.set()
        reloader.join(5)
        for producer in producers:
            producer.join(5)
        manager.shutdown()

    records = [
        json.loads(line)
        for path in tmp_path.rglob("*.log*")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    expected = ["initial", *[f"old-{i}" for i in range(3)], *[f"new-{i}" for i in range(3)]]
    assert sorted(record["message"] for record in records) == sorted(expected)
    assert {record["trace_id"] for record in records if record["message"].startswith("old-")} == {
        "shared"
    }
    assert {record["trace_id"] for record in records if record["message"].startswith("new-")} == {
        "new"
    }
    assert "Logging error" not in capsys.readouterr().err


@pytest.mark.parametrize("next_action", ["configure", "bootstrap", "shutdown"])
def test_lifecycle_calls_serialize_without_blocking_live_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, next_action: str
) -> None:
    """Prevent a pending preparation from overwriting a later lifecycle request."""
    manager = LogManager()
    manager.configure(_config(tmp_path, "old"))
    preparing, release = Event(), Event()
    second_started, second_finished = Event(), Event()
    failures = []

    class _PausedListener(SafeQueueListener):
        def start(self):
            super().start()
            preparing.set()
            assert release.wait(5)

    def first_call():
        try:
            manager.configure(_config(tmp_path, "first", use_queue=True))
        except BaseException as exc:
            failures.append(exc)

    def second_call():
        second_started.set()
        try:
            if next_action == "configure":
                manager.configure(_config(tmp_path, "second"))
            elif next_action == "bootstrap":
                manager.bootstrap(app_name="second")
            else:
                manager.shutdown()
        except BaseException as exc:
            failures.append(exc)
        finally:
            second_finished.set()

    monkeypatch.setattr(manager_module, "SafeQueueListener", _PausedListener)
    first = Thread(target=first_call, daemon=True)
    second = Thread(target=second_call, daemon=True)
    try:
        first.start()
        assert preparing.wait(5)
        second.start()
        assert second_started.wait(5)
        assert not second_finished.wait(0.1)
        assert manager.stage is LogStage.PRODUCTION
        assert manager._config.app_name == "old"
        release.set()
        first.join(5)
        second.join(5)
        assert not first.is_alive() and not second.is_alive()
        assert not failures
        if next_action == "shutdown":
            assert manager.stage is LogStage.SHUTDOWN
        else:
            assert manager._config.app_name == "second"
            expected = LogStage.PRODUCTION if next_action == "configure" else LogStage.BOOTSTRAP
            assert manager.stage is expected
    finally:
        release.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
        manager.shutdown()


def test_candidate_file_open_failure_keeps_old_stream_usable(tmp_path: Path) -> None:
    """Preserve old output when a delayed file fails to open at handover."""
    manager = LogManager()
    manager.configure(_config(tmp_path, "old", use_queue=True))
    logger = manager.get_logger("transaction.file-open")
    logger.info("before")
    previous_listener = manager._queue_listener
    try:
        with pytest.raises(OSError):
            manager.configure(_config(tmp_path, "candidate", total_file=tmp_path, use_queue=True))
        assert manager._config.app_name == "old"
        assert manager._queue_listener is previous_listener
        assert previous_listener._thread.is_alive()
        assert get_default_trace_id() == "old"
        logger.debug("after")
    finally:
        manager.shutdown()
    assert [record["message"] for record in _records(tmp_path, "old")] == ["before", "after"]
