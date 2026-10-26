"""Repeatable CPU/latency logging benchmark: stdlib only, no global config mutation.

Run against any source tree with:
    PYTHONPATH=. python benchmarks/logging_benchmark.py --count 12000 --repeats 5
    PYTHONPATH=. python benchmarks/logging_benchmark.py --count 12000 --profile

For baseline comparisons, execute the SAME benchmark script under the same
interpreter and hardware, with PYTHONPATH pointing at the respective checkout.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import os
import pstats
import statistics
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

from cells.log import LogConfig
from cells.log.manager import LogManager


def run_once(count: int, *, queued: bool, sink: str) -> dict[str, float | int | bool]:
    """Measure producer/drain time across console, aggregate and module sinks."""
    with tempfile.TemporaryDirectory(prefix="cells-bench-") as directory:
        manager = LogManager()
        config = LogConfig(
            app_name="bench",
            enable_console=(sink in {"null", "dual", "triple"}),
            console_to_stderr=False,
            enable_total_file=(sink in {"file", "dual", "triple"}),
            enable_module_file=(sink in {"module", "triple"}),
            log_dir=Path(directory),
            use_queue=queued,
            queue_capacity=count * 2,
            console_include_fields=True,
        )
        manager.configure(config)
        log = manager.get_logger("bench.worker")
        start = time.perf_counter_ns()
        for i in range(count):
            log.info("measurement", iteration=i, latency_ms=1.25, status="success")
        producer_end = time.perf_counter_ns()
        if queued:
            stats = manager.get_queue_stats()
        else:
            stats = {"dropped_records": 0}
        manager.shutdown()
        end = time.perf_counter_ns()
        return {
            "queued": queued,
            "sink": sink,
            "producer_us_per_event": (producer_end - start) / count / 1000,
            "end_to_end_us_per_event": (end - start) / count / 1000,
            "dropped": stats["dropped_records"],
        }


def main() -> None:
    """Run each work profile multiple times and report medians as newline JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=12000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if args.count < 1 or args.repeats < 1:
        parser.error("count and repeats must be >= 1")
    results = []
    with open(os.devnull, "w", encoding="utf-8") as null:
        with redirect_stdout(null):
            for sink in ("null", "file", "dual", "module", "triple"):
                for queued in (False, True):
                    for _ in range(1 if args.profile else args.repeats):
                        if args.profile:
                            profiler = cProfile.Profile()
                            profiler.enable()
                        result = run_once(args.count, queued=queued, sink=sink)
                        if args.profile:
                            profiler.disable()
                            report = io.StringIO()
                            pstats.Stats(profiler, stream=report).sort_stats("cumtime").print_stats(
                                20
                            )
                            print(
                                f"PROFILE sink={sink}, queued={queued}\n{report.getvalue()}",
                                file=sys.stderr,
                            )
                        results.append(result)
    for sink in ("null", "file", "dual", "module", "triple"):
        for queued in (False, True):
            rows = [r for r in results if r["sink"] == sink and r["queued"] == queued]
            print(
                json.dumps(
                    {
                        "sink": sink,
                        "queued": queued,
                        "count": args.count,
                        "repeats": len(rows),
                        "producer_us_per_event_median": round(
                            statistics.median(r["producer_us_per_event"] for r in rows), 3
                        ),
                        "end_to_end_us_per_event_median": round(
                            statistics.median(r["end_to_end_us_per_event"] for r in rows), 3
                        ),
                        "dropped_max": max(r["dropped"] for r in rows),
                    },
                    sort_keys=True,
                )
            )


if __name__ == "__main__":
    main()
