"""A stdio MCP server with one tool, for the MCP servers tests (#960)."""

from fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool
def ping(marker: str = "") -> str:
    """Answer pong and the marker."""
    return f"pong {marker}".strip()


if __name__ == "__main__":
    mcp.run(show_banner=False)
