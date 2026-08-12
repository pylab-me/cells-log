from __future__ import annotations

import logging
import queue
import time

from cells.log.handlers import LocalQueueHandler, SafeQueueListener, make_log_queue


class CollectingHandler(logging.Handler):
    """Store handled messages for queue policy assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def _handler(
    log_queue: queue.Queue[logging.LogRecord],
    policy: str,
    fallback: CollectingHandler,
) -> LocalQueueHandler:
    return LocalQueueHandler(
        log_queue,
        full_policy=policy,
        block_timeout=0.01,
        fallback_handlers=(fallback,),
    )


def test_log_queue_is_bounded() -> None:
    log_queue = make_log_queue(3)

    assert log_queue.maxsize == 3


def test_drop_policy_counts_full_queue_records() -> None:
    log_queue = make_log_queue(1)
    fallback = CollectingHandler()
    handler = _handler(log_queue, "drop", fallback)
    handler.enqueue(_record("first"))
    handler.enqueue(_record("dropped"))

    assert handler.dropped_records == 1
    assert fallback.messages == []


def test_block_policy_times_out_and_counts_drop() -> None:
    log_queue = make_log_queue(1)
    fallback = CollectingHandler()
    handler = _handler(log_queue, "block", fallback)
    handler.enqueue(_record("first"))

    started = time.monotonic()
    handler.enqueue(_record("blocked"))
    elapsed = time.monotonic() - started

    assert elapsed >= 0.005
    assert handler.dropped_records == 1


def test_sync_fallback_writes_when_queue_is_full() -> None:
    log_queue = make_log_queue(1)
    fallback = CollectingHandler()
    handler = _handler(log_queue, "sync_fallback", fallback)
    handler.enqueue(_record("queued"))
    handler.enqueue(_record("fallback"))

    assert handler.dropped_records == 0
    assert fallback.messages == ["fallback"]


def test_listener_can_stop_with_full_bounded_queue() -> None:
    log_queue = make_log_queue(1)
    fallback = CollectingHandler()
    listener = SafeQueueListener(log_queue, fallback)
    listener.start()
    log_queue.put(_record("queued"), timeout=1)
    listener.stop()

    assert fallback.messages == ["queued"]
