"""
Sovereign Mini Datacenter — Model Context Protocol (MCP) JSON-RPC 2.0 Server.
Implements standard MCP 2024-11-05 stdio transport protocol.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, TextIO

from sovereign_dc import __version__
from sovereign_dc.mcp.prompts import get_mcp_prompts
from sovereign_dc.mcp.resources import get_mcp_resources
from sovereign_dc.mcp.tools import get_mcp_tools

logger = logging.getLogger("smdc.mcp")

# Standard MCP Protocol Version
MCP_PROTOCOL_VERSION = "2024-11-05"


class MCPServer:
    """Standard Model Context Protocol (MCP) JSON-RPC 2.0 Server."""

    def __init__(self, server_name: str = "sovereign-mini-datacenter", version: str = __version__) -> None:
        self.server_name = server_name
        self.version = version
        self.tools = {t.name: t for t in get_mcp_tools()}
        self.resources = {r.uri: r for r in get_mcp_resources()}
        self.prompts = {p.name: p for p in get_mcp_prompts()}
        self.initialized = False

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Processes a single JSON-RPC 2.0 request or notification."""
        # Check for JSON-RPC version
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if not method:
            return self._error_response(req_id, -32600, "Invalid Request: Missing 'method'")

        # 1. Initialize Handshake
        if method == "initialize":
            self.initialized = True
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": self.server_name,
                        "version": self.version,
                    },
                },
            }

        # 2. Initialized Notification (no response expected)
        if method == "notifications/initialized":
            logger.info("MCP client acknowledged initialization.")
            return None

        # 3. Ping
        if method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {},
            }

        # 4. Tools: list & call
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": t.input_schema,
                        }
                        for t in self.tools.values()
                    ]
                },
            }

        if method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})

            if not tool_name or tool_name not in self.tools:
                return self._error_response(req_id, -32601, f"Tool '{tool_name}' not found")

            tool = self.tools[tool_name]
            try:
                result_data = tool.handler(tool_args)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result_data, indent=2)
                                if isinstance(result_data, (dict, list))
                                else str(result_data),
                            }
                        ],
                        "isError": False,
                    },
                }
            except Exception as e:
                logger.error("Error executing tool '%s': %s", tool_name, e)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error executing {tool_name}: {e}"}],
                        "isError": True,
                    },
                }

        # 5. Resources: list & read
        if method == "resources/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resources": [
                        {
                            "uri": r.uri,
                            "name": r.name,
                            "description": r.description,
                            "mimeType": r.mime_type,
                        }
                        for r in self.resources.values()
                    ]
                },
            }

        if method == "resources/read":
            uri = params.get("uri")
            if not uri or uri not in self.resources:
                return self._error_response(req_id, -32602, f"Resource URI '{uri}' not found")

            resource = self.resources[uri]
            try:
                content_text = resource.reader()
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "contents": [
                            {
                                "uri": resource.uri,
                                "mimeType": resource.mime_type,
                                "text": content_text,
                            }
                        ]
                    },
                }
            except Exception as e:
                logger.error("Error reading resource '%s': %s", uri, e)
                return self._error_response(req_id, -32603, f"Internal error reading resource: {e}")

        # 6. Prompts: list & get
        if method == "prompts/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "prompts": [
                        {
                            "name": p.name,
                            "description": p.description,
                            "arguments": [
                                {
                                    "name": a.name,
                                    "description": a.description,
                                    "required": a.required,
                                }
                                for a in p.arguments
                            ],
                        }
                        for p in self.prompts.values()
                    ]
                },
            }

        if method == "prompts/get":
            prompt_name = params.get("name")
            prompt_args = params.get("arguments", {})

            if not prompt_name or prompt_name not in self.prompts:
                return self._error_response(req_id, -32601, f"Prompt '{prompt_name}' not found")

            prompt = self.prompts[prompt_name]
            try:
                messages = prompt.builder(prompt_args)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "description": prompt.description,
                        "messages": messages,
                    },
                }
            except Exception as e:
                logger.error("Error building prompt '%s': %s", prompt_name, e)
                return self._error_response(req_id, -32603, f"Internal error building prompt: {e}")

        # Unrecognized method
        return self._error_response(req_id, -32601, f"Method '{method}' not found")

    def _error_response(self, req_id: Any, code: int, message: str) -> dict[str, Any]:
        """Formats a standard JSON-RPC 2.0 error object."""
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    def run_stdio(self, in_stream: TextIO = sys.stdin, out_stream: TextIO = sys.stdout) -> None:
        """Starts the standard I/O loop reading line-delimited JSON-RPC messages."""
        logger.info("Starting Sovereign Mini Datacenter MCP stdio server (%s)...", self.server_name)

        while True:
            try:
                line = in_stream.readline()
                if not line:
                    break  # EOF reached

                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line)
                except json.JSONDecodeError as err:
                    err_response = self._error_response(None, -32700, f"Parse error: {err}")
                    out_stream.write(json.dumps(err_response) + "\n")
                    out_stream.flush()
                    continue

                resp: dict[str, Any] | None = self.handle_request(request)
                if resp is not None:
                    out_stream.write(json.dumps(resp) + "\n")
                    out_stream.flush()

            except (KeyboardInterrupt, SystemExit):
                break
            except Exception as err:
                logger.error("Fatal error in MCP stdio loop: %s", err)
                break
