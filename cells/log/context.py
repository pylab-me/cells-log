"""Context propagation for structured logs."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_trace_id_var: ContextVar[str | None] = ContextVar("cells_log_trace_id", default=None)
_context_fields_var: ContextVar[dict[str, Any] | None] = ContextVar(
    "cells_log_context_fields",
    default=None,
)
_default_trace_id = "system"


def set_default_trace_id(default_trace_id: str) -> None:
    """Set the process-wide fallback used when a context has no trace id."""
    global _default_trace_id
    _default_trace_id = default_trace_id


def get_default_trace_id() -> str:
    """Return the process fallback independently of the calling context."""
    return _default_trace_id


def get_trace_id() -> str:
    """Return the context's nonempty trace ID or the last activated process fallback."""
    return _trace_id_var.get() or _default_trace_id


def set_trace_id(trace_id: str) -> Token[str | None]:
    """Set the current context's trace ID and return a token to restore its old value.

    Call reset_trace_id(token) in finally when the request or task ends; reset
    nested assignments in reverse order. ContextVar isolates async task assignments;
    callers manage propagation across threads. An empty string makes get_trace_id()
    use the configured fallback.
    """
    return _trace_id_var.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    """Restore the prior trace ID using a token from this context exactly once."""
    _trace_id_var.reset(token)


def bind_log_context(**fields: Any) -> Token[dict[str, Any] | None]:
    """Merge request or task fields and return a reset token; new values win.

    Restore with reset_log_context(token) in finally. Only the field dictionary
    is copied; nested objects remain shared. Fields from logger.bind() and the
    individual log call take precedence over these context fields.
    """
    current = dict(_context_fields_var.get() or {})
    current.update(fields)
    return _context_fields_var.set(current)


def reset_log_context(token: Token[dict[str, Any] | None]) -> None:
    """Restore prior fields using the bind_log_context token once in its original context."""
    _context_fields_var.reset(token)


def get_log_context() -> dict[str, Any]:
    """Return a shallow copy whose key changes do not affect the context dictionary."""
    return dict(_context_fields_var.get() or {})
