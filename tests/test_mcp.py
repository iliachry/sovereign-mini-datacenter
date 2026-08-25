"""
Unit and Integration Tests for Sovereign Mini Datacenter MCP (Model Context Protocol) Server.
Verifies JSON-RPC 2.0 protocol handling, 10 native tools, 5 resources, 3 prompts,
error responses, stdio transport, and CLI commands.
"""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from sovereign_dc.cli import (
    cmd_mcp_prompts,
    cmd_mcp_resources,
    cmd_mcp_test,
    cmd_mcp_tools,
)
from sovereign_dc.mcp import (
    MCP_PROTOCOL_VERSION,
    MCPServer,
)


class TestMCPServerProtocol(unittest.TestCase):
    """Tests JSON-RPC 2.0 protocol compliance of MCPServer."""

    def setUp(self) -> None:
        self.server = MCPServer()

    def test_initialize_handshake(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }
        res = self.server.handle_request(req)
        self.assertIsNotNone(res)
        self.assertEqual(res["jsonrpc"], "2.0")
        self.assertEqual(res["id"], "req-1")
        self.assertIn("result", res)
        self.assertEqual(res["result"]["protocolVersion"], MCP_PROTOCOL_VERSION)
        self.assertIn("serverInfo", res["result"])
        self.assertIn("capabilities", res["result"])
        self.assertTrue(self.server.initialized)

    def test_notifications_initialized(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        res = self.server.handle_request(req)
        self.assertIsNone(res)

    def test_ping(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "ping",
        }
        res = self.server.handle_request(req)
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], 42)
        self.assertEqual(res["result"], {})

    def test_invalid_request_missing_method(self) -> None:
        req = {"jsonrpc": "2.0", "id": 100}
        res = self.server.handle_request(req)
        self.assertIsNotNone(res)
        self.assertIn("error", res)
        self.assertEqual(res["error"]["code"], -32600)

    def test_unknown_method(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "non_existent_method",
        }
        res = self.server.handle_request(req)
        self.assertIsNotNone(res)
        self.assertIn("error", res)
        self.assertEqual(res["error"]["code"], -32601)


class TestMCPTools(unittest.TestCase):
    """Tests all 10 MCP Tools and their handlers."""

    def setUp(self) -> None:
        self.server = MCPServer()

    def test_tools_list(self) -> None:
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        res = self.server.handle_request(req)
        self.assertIsNotNone(res)
        tools = res["result"]["tools"]
        self.assertEqual(len(tools), 13)
        tool_names = [t["name"] for t in tools]
        self.assertIn("get_telemetry", tool_names)
        self.assertIn("get_system_status", tool_names)
        self.assertIn("set_load_shedding", tool_names)
        self.assertIn("query_market_pricing", tool_names)
        self.assertIn("get_wallet_balances", tool_names)
        self.assertIn("spool_dtn_bundle", tool_names)
        self.assertIn("predict_satellite_passes", tool_names)
        self.assertIn("query_knowledge_indexer", tool_names)
        self.assertIn("run_security_audit", tool_names)
        self.assertIn("dispatch_technician_alert", tool_names)
        self.assertIn("list_enterprise_apps", tool_names)
        self.assertIn("manage_enterprise_app", tool_names)
        self.assertIn("scaffold_enterprise_app", tool_names)

    def test_call_get_telemetry(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_telemetry", "arguments": {}},
        }
        res = self.server.handle_request(req)
        self.assertFalse(res["result"]["isError"])
        content_json = json.loads(res["result"]["content"][0]["text"])
        self.assertIn("power", content_json)
        self.assertIn("thermal", content_json)
        self.assertIn("storage", content_json)
        self.assertIn("gpu", content_json)

    def test_call_get_system_status(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_system_status", "arguments": {}},
        }
        res = self.server.handle_request(req)
        self.assertFalse(res["result"]["isError"])
        content = json.loads(res["result"]["content"][0]["text"])
        self.assertIn("node_id", content)
        self.assertIn("health", content)
        self.assertIn("load_shedding", content)

    def test_call_set_load_shedding(self) -> None:
        for lvl in ["L0", "L1", "L2", "L3", "L4"]:
            req = {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "set_load_shedding", "arguments": {"level": lvl}},
            }
            res = self.server.handle_request(req)
            self.assertFalse(res["result"]["isError"])
            content = json.loads(res["result"]["content"][0]["text"])
            self.assertTrue(content["success"])
            self.assertEqual(content["level"], lvl)

        # Invalid level
        req_bad = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "set_load_shedding", "arguments": {"level": "INVALID"}},
        }
        res_bad = self.server.handle_request(req_bad)
        content_bad = json.loads(res_bad["result"]["content"][0]["text"])
        self.assertFalse(content_bad["success"])

    def test_call_query_market_pricing(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "query_market_pricing", "arguments": {"battery_soc": 90.0, "solar_watts": 1200.0}},
        }
        res = self.server.handle_request(req)
        self.assertFalse(res["result"]["isError"])
        content = json.loads(res["result"]["content"][0]["text"])
        self.assertEqual(content["battery_soc_percent"], 90.0)
        self.assertEqual(content["solar_pv_watts"], 1200.0)
        self.assertIn("service_rates", content)

    def test_call_get_wallet_balances(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "get_wallet_balances", "arguments": {}},
        }
        res = self.server.handle_request(req)
        self.assertFalse(res["result"]["isError"])
        content = json.loads(res["result"]["content"][0]["text"])
        self.assertTrue(content["address_ed25519"].startswith("sov_"))
        self.assertIn("balance_sov", content)

    def test_call_spool_dtn_bundle(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "spool_dtn_bundle",
                "arguments": {
                    "destination": "dtn://ground-station.earth/telemetry",
                    "payload": "MCP_TEST_PAYLOAD",
                    "priority": 2,
                },
            },
        }
        res = self.server.handle_request(req)
        self.assertFalse(res["result"]["isError"])
        content = json.loads(res["result"]["content"][0]["text"])
        self.assertTrue(content["success"])
        self.assertEqual(content["priority"], "EXPEDITED")

    def test_call_predict_satellite_passes(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "predict_satellite_passes",
                "arguments": {"duration_hours": 6.0, "min_elevation_deg": 15.0},
            },
        }
        res = self.server.handle_request(req)
        self.assertFalse(res["result"]["isError"])
        content = json.loads(res["result"]["content"][0]["text"])
        self.assertIn("passes", content)
        self.assertIn("total_passes_found", content)

    def test_call_query_knowledge_indexer(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "query_knowledge_indexer", "arguments": {"query": "cooling loop", "limit": 2}},
        }
        res = self.server.handle_request(req)
        self.assertFalse(res["result"]["isError"])
        content = json.loads(res["result"]["content"][0]["text"])
        self.assertEqual(content["query"], "cooling loop")

    def test_call_run_security_audit(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "run_security_audit", "arguments": {}},
        }
        res = self.server.handle_request(req)
        self.assertFalse(res["result"]["isError"])
        content = json.loads(res["result"]["content"][0]["text"])
        self.assertEqual(content["compliance_score_percent"], 100.0)

    def test_call_dispatch_technician_alert(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "dispatch_technician_alert",
                "arguments": {"message": "Test alert", "severity": "WARNING"},
            },
        }
        res = self.server.handle_request(req)
        self.assertFalse(res["result"]["isError"])
        content = json.loads(res["result"]["content"][0]["text"])
        self.assertTrue(content["dispatched"])
        self.assertIn("channels", content)

    def test_call_unknown_tool(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {"name": "unregistered_tool", "arguments": {}},
        }
        res = self.server.handle_request(req)
        self.assertEqual(res["error"]["code"], -32601)

    def test_call_tool_exception_handling(self) -> None:
        with patch.object(self.server.tools["get_telemetry"], "handler", side_effect=RuntimeError("Simulated Boom")):
            req = {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "tools/call",
                "params": {"name": "get_telemetry", "arguments": {}},
            }
            res = self.server.handle_request(req)
            self.assertTrue(res["result"]["isError"])
            self.assertIn("Simulated Boom", res["result"]["content"][0]["text"])


class TestMCPResources(unittest.TestCase):
    """Tests all 7 MCP Resources."""

    def setUp(self) -> None:
        self.server = MCPServer()

    def test_resources_list(self) -> None:
        req = {"jsonrpc": "2.0", "id": 1, "method": "resources/list"}
        res = self.server.handle_request(req)
        resources = res["result"]["resources"]
        self.assertEqual(len(resources), 7)
        uris = [r["uri"] for r in resources]
        self.assertIn("smdc://telemetry/current", uris)
        self.assertIn("smdc://system/manifest", uris)
        self.assertIn("smdc://economy/market", uris)
        self.assertIn("smdc://space/dtn/spool", uris)
        self.assertIn("smdc://security/pqc/status", uris)
        self.assertIn("smdc://enterprise/apps", uris)
        self.assertIn("smdc://enterprise/schema", uris)

    def test_read_all_resources(self) -> None:
        uris = [
            "smdc://telemetry/current",
            "smdc://system/manifest",
            "smdc://economy/market",
            "smdc://space/dtn/spool",
            "smdc://security/pqc/status",
            "smdc://enterprise/apps",
            "smdc://enterprise/schema",
        ]
        for uri in uris:
            req = {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": uri}}
            res = self.server.handle_request(req)
            self.assertIn("contents", res["result"])
            self.assertEqual(res["result"]["contents"][0]["uri"], uri)
            parsed = json.loads(res["result"]["contents"][0]["text"])
            self.assertEqual(parsed["uri"], uri)

    def test_read_unknown_resource(self) -> None:
        req = {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "smdc://unknown"}}
        res = self.server.handle_request(req)
        self.assertEqual(res["error"]["code"], -32602)

    def test_read_resource_exception_handling(self) -> None:
        with patch.object(
            self.server.resources["smdc://telemetry/current"], "reader", side_effect=ValueError("Disk Error")
        ):
            req = {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "resources/read",
                "params": {"uri": "smdc://telemetry/current"},
            }
            res = self.server.handle_request(req)
            self.assertEqual(res["error"]["code"], -32603)


class TestMCPPrompts(unittest.TestCase):
    """Tests all 4 MCP Prompt Templates."""

    def setUp(self) -> None:
        self.server = MCPServer()

    def test_prompts_list(self) -> None:
        req = {"jsonrpc": "2.0", "id": 1, "method": "prompts/list"}
        res = self.server.handle_request(req)
        prompts = res["result"]["prompts"]
        self.assertEqual(len(prompts), 4)
        names = [p["name"] for p in prompts]
        self.assertIn("diagnose_power_incident", names)
        self.assertIn("plan_compute_workload", names)
        self.assertIn("prepare_space_transmission", names)
        self.assertIn("onboard_enterprise_workload", names)

    def test_get_prompts(self) -> None:
        req1 = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "prompts/get",
            "params": {"name": "diagnose_power_incident", "arguments": {"symptom": "Rapid battery drop"}},
        }
        res1 = self.server.handle_request(req1)
        self.assertIn("messages", res1["result"])
        self.assertIn("Rapid battery drop", res1["result"]["messages"][0]["content"]["text"])

        req2 = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "prompts/get",
            "params": {
                "name": "plan_compute_workload",
                "arguments": {"workload": "Batch Embedding", "estimated_kwh": "2.1"},
            },
        }
        res2 = self.server.handle_request(req2)
        self.assertIn("Batch Embedding", res2["result"]["messages"][0]["content"]["text"])

        req3 = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "prompts/get",
            "params": {
                "name": "prepare_space_transmission",
                "arguments": {"destination": "dtn://orbit.space/science"},
            },
        }
        res3 = self.server.handle_request(req3)
        self.assertIn("dtn://orbit.space/science", res3["result"]["messages"][0]["content"]["text"])

        req4 = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "prompts/get",
            "params": {
                "name": "onboard_enterprise_workload",
                "arguments": {"project_description": "Smart Grid Ingestion"},
            },
        }
        res4 = self.server.handle_request(req4)
        self.assertIn("Smart Grid Ingestion", res4["result"]["messages"][0]["content"]["text"])

    def test_get_unknown_prompt(self) -> None:
        req = {"jsonrpc": "2.0", "id": 5, "method": "prompts/get", "params": {"name": "unknown_prompt"}}
        res = self.server.handle_request(req)
        self.assertEqual(res["error"]["code"], -32601)

    def test_get_prompt_exception_handling(self) -> None:
        with patch.object(
            self.server.prompts["diagnose_power_incident"], "builder", side_effect=TypeError("Bad arguments")
        ):
            req = {"jsonrpc": "2.0", "id": 6, "method": "prompts/get", "params": {"name": "diagnose_power_incident"}}
            res = self.server.handle_request(req)
            self.assertEqual(res["error"]["code"], -32603)


class TestMCPServerStdioLoop(unittest.TestCase):
    """Tests line-delimited JSON-RPC stdio event loop."""

    def test_stdio_loop_execution(self) -> None:
        input_data = (
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
            + "\n"
            + "MALFORMED_JSON_LINE\n"
            + "\n"
        )
        in_stream = io.StringIO(input_data)
        out_stream = io.StringIO()

        server = MCPServer()
        server.run_stdio(in_stream=in_stream, out_stream=out_stream)

        output_lines = [json.loads(line) for line in out_stream.getvalue().strip().split("\n") if line]
        self.assertEqual(len(output_lines), 3)
        self.assertEqual(output_lines[0]["id"], 1)
        self.assertEqual(output_lines[1]["id"], 2)
        self.assertEqual(output_lines[2]["error"]["code"], -32700)


class TestMCPCLICommands(unittest.TestCase):
    """Tests CLI subcommands for MCP."""

    def test_cli_tools(self) -> None:
        class Args:
            pass

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_mcp_tools(Args())
            output = mock_out.getvalue()
            self.assertIn("get_telemetry", output)
            self.assertIn("Registered MCP Tools", output)

    def test_cli_resources(self) -> None:
        class Args:
            pass

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_mcp_resources(Args())
            output = mock_out.getvalue()
            self.assertIn("smdc://telemetry/current", output)
            self.assertIn("Registered MCP Resources", output)

    def test_cli_prompts(self) -> None:
        class Args:
            pass

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_mcp_prompts(Args())
            output = mock_out.getvalue()
            self.assertIn("diagnose_power_incident", output)
            self.assertIn("Registered MCP Prompts", output)

    def test_cli_test(self) -> None:
        class Args:
            pass

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_mcp_test(Args())
            output = mock_out.getvalue()
            self.assertIn("All Model Context Protocol (MCP) diagnostic checks passed", output)


if __name__ == "__main__":
    unittest.main()
