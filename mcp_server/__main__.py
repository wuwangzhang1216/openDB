"""Entry point: python -m mcp_server [--transport stdio|streamable_http] [--port 8200]"""

from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="MuseDB MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable_http"],
        default="stdio",
        help="Transport type (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8200,
        help="Port for streamable_http transport (default: 8200)",
    )
    args = parser.parse_args()

    from mcp_server.server import mcp

    if args.transport == "streamable_http":
        mcp.run(transport="streamable_http", port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
