"""Dependency-free, read-only browser view for Petri-net projections."""

from .server import RequestResponse, handle_request, serve_projection

__all__ = ("RequestResponse", "handle_request", "serve_projection")
