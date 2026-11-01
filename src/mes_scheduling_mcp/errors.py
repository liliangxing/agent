"""Typed errors and error sanitization.

Real-world MCP servers never leak stack traces, URLs, hostnames, or
credentials back to a (possibly remote) client. Every domain error is
wrapped in a :class:`McpError` whose message is safe to show, and
unexpected exceptions are sanitised to a generic message while the full
traceback stays in the server log.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("mes_scheduling_mcp.errors")

# Pattern for anything that looks like a URL or a host:port token.
_URL_RE = re.compile(r"https?://[^\s\"']+|[\w.-]+:\d{2,5}")


class McpError(Exception):
    """Base type for errors safe to return to a client."""

    code = "error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(McpError):
    code = "validation_error"


class NotFoundError(McpError):
    code = "not_found"


class AccessDeniedError(McpError):
    code = "access_denied"


class AuthenticationError(McpError):
    code = "authentication_error"


def sanitize_error(exc: BaseException) -> McpError:
    """Convert any exception into a client-safe :class:`McpError`."""
    if isinstance(exc, McpError):
        return exc
    # Strip anything URL- or host-like out of an unexpected message before
    # falling back; keep the real details in the server log only.
    logger.exception("unexpected tool error", exc_info=exc)
    return McpError("internal error; see server log for details")