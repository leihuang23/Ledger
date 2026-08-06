"""MCP server adapter for Ledger's existing tool registry.

This package is a thin adapter layer only: every governed MCP tool dispatches
through ``app.tools.registry.BUILTIN_TOOL_BY_ID`` so the registry remains the
single source of truth for tool ids, Pydantic schemas, permission scopes, and
implementations. No second tool system is introduced.
"""
