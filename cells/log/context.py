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


def get_trace_id() -> str:
    """Return the explicit trace id or the latest configured fallback."""
    return _trace_id_var.get() or _default_trace_id


def set_trace_id(trace_id: str) -> Token[str | None]:
    """Set trace id for current context."""
    return _trace_id_var.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    """Reset trace id token returned by set_trace_id."""
    _trace_id_var.reset(token)


def bind_log_context(**fields: Any) -> Token[dict[str, Any] | None]:
    """Merge fields into the current context and return a reset token."""
    current = dict(_context_fields_var.get() or {})
    current.update(fields)
    return _context_fields_var.set(current)


def reset_log_context(token: Token[dict[str, Any] | None]) -> None:
    """Reset context fields token returned by bind_log_context."""
    _context_fields_var.reset(token)


def get_log_context() -> dict[str, Any]:
    """Return a copy of the current context fields."""
    return dict(_context_fields_var.get() or {})
