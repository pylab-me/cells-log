"""cells-log release version and runnable end-to-end usage example.

The example is documentation, not executable import side effects. Run its
contents in an application or copy them to a standalone script::

    from pathlib import Path
    from cells.log import (
        LogConfig, bind_log_context, configure_logging, flush_logging,
        get_logger, get_queue_stats, reset_log_context, reset_trace_id,
        set_trace_id, shutdown_logging,
    )

    def redact(fields: dict) -> dict:
        "Remove secrets before JSON serialization."
        fields.pop("password", None)
        return fields

    configure_logging(LogConfig(
        app_name="example",
        level="DEBUG",
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
    ))
    trace_token = set_trace_id("request-001")
    context_token = bind_log_context(service="billing", tenant="sample")
    try:
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
        reset_log_context(context_token)
        reset_trace_id(trace_token)
        shutdown_logging()

Trace and bound fields are copied per record; ContextVar values are isolated
per asynchronous task. Synchronous overflow fallback can block and reorder.
"""

__VERSION__ = "0.3.2"
