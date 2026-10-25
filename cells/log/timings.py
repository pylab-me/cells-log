"""Request timing collection and structured summaries."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from threading import Lock
from time import perf_counter
from typing import Any, Literal

from cells.log.levels import parse_log_level

__all__ = ["TimingCollector"]


class TimingCollector:
    """Collect stage durations and emit one summary when the scope ends.

    Instances and measurement scopes are single-use. A collector must be active
    before creating or entering measurements. Repeated metric names accumulate
    milliseconds; declared unused metrics remain None. Generated timing values
    override same-named business fields, and the total metric cannot be measured.
    Stages measure inclusive wall time; nested or overlapping stage sums may
    exceed the independent total and are never normalized.

    Pass the collector explicitly to worker threads. Wait for workers before
    leaving the scope to capture their complete durations; outstanding measures
    are frozen at the collector's exit time. Later exits cannot change the summary.
    Context is captured by the logging pipeline at emission, as for ordinary logs.

    Args:
        logger: Standard-library logger receiving the summary.
        event: Summary message and prefix for the generated total metric.
        level: Logging level, checked once when entering the collector.
        metrics: Optional names for stages that should appear even when unused.
        fields: Business fields, shallow-copied when constructing the collector.
    """

    def __init__(
        self,
        logger: logging.Logger,
        event: str,
        *,
        level: str | int = "DEBUG",
        metrics: Iterable[str] = (),
        fields: dict[str, Any] | None = None,
    ) -> None:
        """Initialize an independent collector without reading the clock."""
        if not isinstance(event, str) or not event:
            raise ValueError("event must be a nonempty string")
        self._logger = logger
        self._event = event
        self._level = parse_log_level(level)
        self._total_metric = f"{event}.total_ms"
        self._fields = dict(fields or {})
        self._metrics: dict[str, float | None] = {}
        for metric in metrics:
            self._validate_metric(metric)
            self._metrics[metric] = None
        self._lock = Lock()
        self._state = "new"
        self._enabled = False
        self._started = 0.0
        self._running: dict[_Measurement, float] = {}

    def __enter__(self) -> TimingCollector:
        """Enter a single-use scope and read the clock only when enabled."""
        with self._lock:
            if self._state != "new":
                raise RuntimeError("timing collectors are single-use")
            self._enabled = self._logger.isEnabledFor(self._level)
            if self._enabled:
                self._started = perf_counter()
            self._state = "active"
        return self

    async def __aenter__(self) -> TimingCollector:
        """Enter asynchronously without creating tasks or changing context."""
        return self.__enter__()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any,
    ) -> Literal[False]:
        """Emit the summary without capturing or suppressing business exceptions."""
        return self._finish(exception)

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any,
    ) -> Literal[False]:
        """Exit asynchronously while preserving the caller's exception."""
        return self._finish(exception)

    def measure(self, metric: str) -> _Measurement:
        """Create a stage scope supporting with and async with.

        Args:
            metric: Exact output field name, typically ending in "_ms".

        Returns:
            A single-use measurement scope.

        Raises:
            RuntimeError: If the collector is not active.
            ValueError: If the metric is empty or collides with the total.
        """
        self._validate_metric(metric)
        with self._lock:
            self._require_active()
        return _Measurement(self, metric)

    def _validate_metric(self, metric: str) -> None:
        if not isinstance(metric, str) or not metric:
            raise ValueError("metric must be a nonempty string")
        if metric == self._total_metric:
            raise ValueError("metric collides with the generated total")

    def _require_active(self) -> None:
        if self._state != "active":
            raise RuntimeError("measurements require an active timing collector")

    def _start_measure(self, measurement: _Measurement) -> None:
        with self._lock:
            self._require_active()
            if measurement.entered:
                raise RuntimeError("measurement scopes are single-use")
            measurement.entered = True
            if self._enabled:
                self._running[measurement] = perf_counter()

    def _finish_measure(self, measurement: _Measurement) -> None:
        with self._lock:
            if not measurement.entered or measurement.finished:
                raise RuntimeError("measurement scope is not active")
            measurement.finished = True
            started = self._running.pop(measurement, None)
            if started is not None:
                self._accumulate(measurement.metric, perf_counter() - started)

    def _accumulate(self, metric: str, elapsed_seconds: float) -> None:
        self._metrics[metric] = (self._metrics.get(metric) or 0.0) + elapsed_seconds * 1000.0

    def _finish(self, exception: BaseException | None) -> Literal[False]:
        with self._lock:
            self._require_active()
            self._state = "closed"
            if not self._enabled:
                return False
            ended = perf_counter()
            for measurement, started in self._running.items():
                self._accumulate(measurement.metric, ended - started)
            self._running.clear()
            summary = {
                **self._fields,
                **self._metrics,
                self._total_metric: (ended - self._started) * 1000.0,
            }
        try:
            self._logger.log(
                self._level,
                self._event,
                extra={"_fields": summary},
                stacklevel=3,
            )
        except Exception:
            # A failing sink must not replace an exception from the measured work.
            if exception is None:
                raise
        return False


class _Measurement:
    """Manage a stage's start, completion and single-use lifecycle."""

    def __init__(self, collector: TimingCollector, metric: str) -> None:
        self.collector = collector
        self.metric = metric
        self.entered = False
        self.finished = False

    def __enter__(self) -> _Measurement:
        self.collector._start_measure(self)
        return self

    async def __aenter__(self) -> _Measurement:
        return self.__enter__()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any,
    ) -> Literal[False]:
        self.collector._finish_measure(self)
        return False

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any,
    ) -> Literal[False]:
        return self.__exit__(exception_type, exception, traceback)
