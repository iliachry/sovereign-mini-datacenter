"""
Sovereign Mini Datacenter — Model Context Protocol (MCP) Package.
Exposes real-time micro-datacenter telemetry, load-shedding control, dynamic pricing,
space DTN satellite routing, and PQC security to external AI assistants via standard JSON-RPC 2.0.
"""

from sovereign_dc.mcp.prompts import MCPPrompt, MCPPromptArgument, get_mcp_prompts
from sovereign_dc.mcp.resources import MCPResource, get_mcp_resources
from sovereign_dc.mcp.server import MCP_PROTOCOL_VERSION, MCPServer
from sovereign_dc.mcp.tools import MCPTool, get_mcp_tools

__all__ = [
    "MCPServer",
    "MCPTool",
    "MCPResource",
    "MCPPrompt",
    "MCPPromptArgument",
    "MCP_PROTOCOL_VERSION",
    "get_mcp_tools",
    "get_mcp_resources",
    "get_mcp_prompts",
]
