"""cells-log release version and runnable end-to-end usage example.

The example is documentation, not executable import side effects. Run its
contents in an application or copy them to a standalone script::

    from pathlib import Path
    from cells.log import (
        LogConfig, configure_logging, flush_logging, get_logger,
        get_queue_stats, log_context, shutdown_logging,
    )

    def redact(fields: dict) -> dict:
        "Remove secrets before JSON serialization."
        fields.pop("password", None)
        return fields

    configure_logging(LogConfig(
        app_name="example",
        level="DEBUG",
        format="json",
        format_for_console="text",
        format_for_file="json",
        log_dir=Path("./logs"),
        enable_console=True,
        enable_module_file=True,
        enable_total_file=True,
        rotation="size",
        size_limit="5MB",
        use_queue=True,
        queue_capacity=1024,
        queue_full_policy="drop",
        queue_fallback_min_level="ERROR",
        field_processor=redact,
    ), strict=True)
    try:
        with log_context(trace_id="request-001", service="billing", tenant="sample"):
            log = get_logger("example.checkout").bind(operation="checkout")
            log.info("processing payment", order_id=42, password="hidden")
            with log.timings("payment", level="INFO") as timing:
                with timing.measure("validate_ms"):
                    log.debug("validated", order_id=42)
            try:
                raise RuntimeError("payment provider unavailable")
            except RuntimeError:
                log.exception("payment failed", order_id=42)
            drained = flush_logging(timeout=3.0)
            log.info("flush complete", drained=drained, stats=get_queue_stats())
    finally:
        shutdown_logging()

Trace and bound fields are copied per record; ContextVar values are isolated
per asynchronous task. Synchronous overflow fallback can block and reorder.
"""

__VERSION__ = "0.3.3"
