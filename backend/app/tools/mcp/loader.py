"""MCP servers as a tool source.

Servers are declared in mcp_servers.yaml (MCP_SERVERS_CONFIG env var overrides
the path; see mcp_servers.example.yaml). Their tools are converted to LangChain
tools by langchain-mcp-adapters and registered in the ordinary tool registry,
so ENABLED_TOOLS filtering and prompt composition apply to them like to any
built-in tool.

Discovery is async, so it cannot happen at import time like the other tool
modules — the FastAPI lifespan awaits connect_mcp_servers() before building
the graph. The MultiServerMCPClient is stateless: each tool call opens a
short-lived session, so there is no connection to keep alive or tear down.
"""

import logging
import os
import re
from pathlib import Path

import yaml

from ..base import ToolSpec, register

logger = logging.getLogger(__name__)

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_DEFAULT_CONFIG = Path(__file__).parents[3] / "mcp_servers.yaml"

_CONNECTION_KEYS = {
    "stdio": ("command", "args", "env", "cwd"),
    "streamable_http": ("url", "headers", "timeout"),
    "sse": ("url", "headers", "timeout"),
}


def _interpolate(value):
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def _load_config() -> list[dict]:
    path = Path(os.getenv("MCP_SERVERS_CONFIG", str(_DEFAULT_CONFIG)))
    if not path.exists():
        return []
    entries = yaml.safe_load(path.read_text()) or []

    servers = []
    for entry in entries:
        name = entry.get("name", "<unnamed>")
        missing = [
            var
            for var in _ENV_RE.findall(yaml.safe_dump(entry))
            if not os.getenv(var)
        ]
        if missing:
            logger.warning(
                "MCP server '%s' skipped: missing env vars %s", name, missing
            )
            continue
        servers.append(_interpolate(entry))
    return servers


async def connect_mcp_servers() -> None:
    """Discover tools from configured MCP servers and register them.

    Called from the FastAPI lifespan before the graph is built. A server that
    fails to connect is logged and skipped so it never blocks startup.
    """
    servers = _load_config()
    if not servers:
        return

    from langchain_mcp_adapters.client import MultiServerMCPClient

    for server in servers:
        name = server["name"]
        transport = server.get("transport", "stdio")
        connection = {"transport": transport}
        for key in _CONNECTION_KEYS.get(transport, ()):
            if key in server:
                connection[key] = server[key]

        client = MultiServerMCPClient({name: connection})
        try:
            tools = await client.get_tools(server_name=name)
        except Exception as exc:
            logger.warning("MCP server '%s' unreachable, skipped: %s", name, exc)
            continue

        for tool in tools:
            register(ToolSpec(tool=tool, prompt_hint=server.get("prompt_hint", "")))
        logger.info(
            "MCP server '%s': registered %d tools: %s",
            name,
            len(tools),
            [t.name for t in tools],
        )
