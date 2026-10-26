# Benchmarks

A stdlib-only benchmark suite comparing `LogConfig(use_queue=False)` and `LogConfig(use_queue=True)` across three output profiles: null console (`stdout` redirected to `/dev/null`), rotating local file, and dual null+file sinks. It also supports `cProfile`.

```bash
PYTHONPATH=. python benchmarks/logging_benchmark.py --count 10000 --repeats 5
PYTHONPATH=. python benchmarks/logging_benchmark.py --count 3000 --profile
```

For historical comparisons, extract v0.2.2 to another directory, copy the **same** script there, execute in the same machine/interpreter, and compare medians. The included JSONL snapshots come from Linux/Python 3.13.5, 10,000 events × 5 repeats; no logs were dropped. They are not statistically significant measurements across machines. `cProfile` output is for hot-path identification, **not** throughput measurement.

All results are end-to-end unless explicitly marked `producer_us_per_event_median`. For queued workloads the producer value excludes listener draining, while end-to-end includes it. Queue performance depends heavily on sink latency and scheduling contention.

## Snapshot interpretation

- Single fast sink: sync and queued performance were broadly comparable between releases; the queue adds overhead for a fast null sink.
- Dual JSON sinks: cached serialization reduces synchronous end-to-end cost in this sample (v0.2.2: 24.593 µs/event; v0.3.0: 17.919 µs/event). Queued dual sinks: 30.673 → 24.565 µs/event. These are local observations, not guaranteed speedups.
- `profile-v0.2.2.txt` / `profile-v0.3.0.txt` show cumulative CPU hot paths. The main costs remain stdlib LogRecord creation and stack inspection, locking/routing, JSON encoding and output.

Do not interpret queue submission latency as end-to-end delivery or add/subtract queue totals to infer external durability. Re-run on the actual production sink and concurrency regime before selecting a delivery mode.
