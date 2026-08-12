from pathlib import Path

from cells.log import (
    LogConfig,
    bind_log_context,
    configure_logging,
    get_logger,
    reset_log_context,
    set_trace_id,
)


def main() -> None:
    configure_logging(
        LogConfig(
            app_name="demo",
            level="DEBUG",
            format="text",
            log_dir=Path("./logs"),
            enable_module_file=True,
            enable_total_file=True,
            total_file="demo.log",
            module_name_prefixes=("demo",),
        )
    )

    set_trace_id("trace-demo-001")
    token = bind_log_context(job_id="job-001", stage="collector")
    try:
        log = get_logger("demo.collector").bind(component="github")
        log.info("collector started", task_count=3)
        log.success("collector finished", duration_ms=18.5)
    finally:
        reset_log_context(token)


if __name__ == "__main__":
    main()
