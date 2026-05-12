"""Context propagation for structured logs."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any


_trace_id_var: ContextVar[str] = ContextVar("cells_log_trace_id", default="system")
_context_fields_var: ContextVar[dict[str, Any]] = ContextVar("cells_log_context_fields", default={})


def set_default_trace_id(default_trace_id: str) -> None:
    """Set trace id for the current context when no explicit trace id exists."""
    if _trace_id_var.get() == "system":
        _trace_id_var.set(default_trace_id)


def get_trace_id() -> str:
    return _trace_id_var.get()


def set_trace_id(trace_id: str) -> Token[str]:
    """Set trace id for current context."""
    return _trace_id_var.set(trace_id)


def reset_trace_id(token: Token[str]) -> None:
    """Reset trace id token returned by set_trace_id."""
    _trace_id_var.reset(token)


def bind_log_context(**fields: Any) -> Token[dict[str, Any]]:
    """Merge fields into the current context and return a reset token."""
    current = dict(_context_fields_var.get())
    current.update(fields)
    return _context_fields_var.set(current)


def reset_log_context(token: Token[dict[str, Any]]) -> None:
    """Reset context fields token returned by bind_log_context."""
    _context_fields_var.reset(token)


def get_log_context() -> dict[str, Any]:
    """Return a copy of the current context fields."""
    return dict(_context_fields_var.get())
