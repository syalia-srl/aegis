"""An MCP server with one tool, for the MCP servers tests (#960): stdio, or
HTTP on a port with ``--http PORT`` (#983)."""

import sys

from fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool
def ping(marker: str = "") -> str:
    """Answer pong and the marker."""
    return f"pong {marker}".strip()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--http"]:
        mcp.run(
            transport="http", host="127.0.0.1", port=int(sys.argv[2]), show_banner=False
        )
    else:
        mcp.run(show_banner=False)
