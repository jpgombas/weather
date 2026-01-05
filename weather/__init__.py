"""Weather package helpers with lazy server imports to avoid runpy warnings.

This module avoids importing `weather.server` at package import time to prevent
`RuntimeWarning` when starting the server with `python -m weather.server` from
another process.
"""

from importlib import import_module
from .client import MCPStdIOClient, MCPClientError

__all__ = [
    "MCPStdIOClient",
    "MCPClientError",
    "mcp",
    "make_nws_request",
    "format_alert",
    "get_alerts",
    "get_forecast",
    "geocode",
    "run_server",
]

# Attributes provided by the server module. We lazily import `weather.server`
# only when one of these attributes is accessed.
_server_attrs = {
    "mcp",
    "make_nws_request",
    "format_alert",
    "get_alerts",
    "get_forecast",
    "geocode",
    "run_server",
}


def _load_server():
    return import_module(".server", __package__)


def __getattr__(name: str):
    if name in _server_attrs:
        return getattr(_load_server(), name)
    raise AttributeError(f"module {__name__} has no attribute {name}")


def __dir__():
    return sorted(list(globals().keys()) + list(_server_attrs))
