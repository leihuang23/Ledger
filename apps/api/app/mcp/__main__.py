"""Entry point for the Ledger MCP server (stdio transport).

Run with ``python -m app.mcp`` or the ``ledger-mcp-server`` console script.
Governed tools require ``MCP_OPERATOR_TOKEN`` in the server process
environment to match the API's ``DEMO_OPERATOR_TOKEN``; in ``APP_ENV=demo``
they fail closed when ``DEMO_OPERATOR_TOKEN`` is unset.
"""

from __future__ import annotations

from app.mcp.server import create_server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
