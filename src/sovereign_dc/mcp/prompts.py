"""
Sovereign Mini Datacenter — Model Context Protocol (MCP) Prompts Registry.
Exposes guided operational, diagnostic, and workload scheduling prompt templates.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class MCPPromptArgument:
    """Represents an argument for an MCP prompt."""

    name: str
    description: str
    required: bool = False


@dataclass
class MCPPrompt:
    """Represents a Model Context Protocol prompt template."""

    name: str
    description: str
    arguments: list[MCPPromptArgument]
    builder: Callable[[dict[str, str]], list[dict[str, Any]]]


# --- Prompt Builders ---


def build_diagnose_power_incident_prompt(args: dict[str, str]) -> list[dict[str, Any]]:
    """Builds an interactive diagnostic prompt for power or thermal anomalies."""
    observed_symptom = args.get("symptom", "Battery SoC dropping rapidly below 30%")
    return [
        {
            "role": "user",
            "content": {
                "type": "text",
                "text": (
                    f"You are the autonomous Sentinel Copilot for the Sovereign Mini Datacenter.\n"
                    f"An operator is reporting the following incident: '{observed_symptom}'.\n\n"
                    f"Please perform a structured root cause analysis (RCA):\n"
                    f"1. Call `get_telemetry` to inspect current solar harvest, battery voltage, AC load, and coolant temperatures.\n"
                    f"2. Check if dynamic load shedding is active via `get_system_status`.\n"
                    f"3. Query the knowledge base via `query_knowledge_indexer` for known troubleshooting procedures.\n"
                    f"4. If battery SoC is critical (< 20%), recommend executing `set_load_shedding(level='L3')`.\n"
                    f"5. If hardware intervention is required, offer to dispatch a technician alert via `dispatch_technician_alert`."
                ),
            },
        }
    ]


def build_plan_compute_workload_prompt(args: dict[str, str]) -> list[dict[str, Any]]:
    """Builds a prompt to schedule heavy AI / LLM batch workloads based on energy reserves."""
    task_desc = args.get("workload", "Fine-tuning local Qwen2.5-Coder model with 50,000 code diffs")
    est_kwh = args.get("estimated_kwh", "3.5")
    return [
        {
            "role": "user",
            "content": {
                "type": "text",
                "text": (
                    f"You are the workload scheduler for the Sovereign Mini Datacenter.\n"
                    f"A user wants to schedule the following batch compute workload: '{task_desc}' (est. energy required: {est_kwh} kWh).\n\n"
                    f"Please evaluate the off-grid feasibility:\n"
                    f"1. Call `get_telemetry` to determine current battery reserves (10.24 kWh LiFePO4 bank) and solar PV harvest.\n"
                    f"2. Call `query_market_pricing` to check if a surplus solar discount (50% OFF) is currently active.\n"
                    f"3. Advise whether to run immediately, throttle to single GPU, or queue for peak solar window (11:00 - 15:00)."
                ),
            },
        }
    ]


def build_prepare_space_transmission_prompt(args: dict[str, str]) -> list[dict[str, Any]]:
    """Builds a prompt for assembling and scheduling satellite DTN bundle transmissions."""
    target_dest = args.get("destination", "dtn://ground-station-alpha.earth/telemetry")
    return [
        {
            "role": "user",
            "content": {
                "type": "text",
                "text": (
                    f"You are the Space Communications Specialist for the Sovereign Mini Datacenter.\n"
                    f"We need to transmit critical telemetry bundles to destination '{target_dest}'.\n\n"
                    f"Please orchestrate the space transmission:\n"
                    f"1. Call `predict_satellite_passes` to identify the next satellite overpass window (AOS/LOS).\n"
                    f"2. Inspect the current NVMe queue via `get_system_status`.\n"
                    f"3. Package and spool the payload using `spool_dtn_bundle` with priority Expedited (2).\n"
                    f"4. Verify post-quantum cryptographic signatures with `run_security_audit`."
                ),
            },
        }
    ]


# --- Registry ---


def get_mcp_prompts() -> list[MCPPrompt]:
    """Returns the full list of registered MCP prompt templates."""
    return [
        MCPPrompt(
            name="diagnose_power_incident",
            description="Perform a structured root-cause analysis for battery depletion, thermal alarms, or solar harvest anomalies.",
            arguments=[
                MCPPromptArgument(
                    name="symptom",
                    description="Observed anomaly or warning (e.g. 'Battery SoC dropping rapidly')",
                    required=False,
                )
            ],
            builder=build_diagnose_power_incident_prompt,
        ),
        MCPPrompt(
            name="plan_compute_workload",
            description="Evaluate solar and battery feasibility for scheduling heavy LLM fine-tuning or batch RAG jobs.",
            arguments=[
                MCPPromptArgument(
                    name="workload",
                    description="Description of the compute task to run",
                    required=True,
                ),
                MCPPromptArgument(
                    name="estimated_kwh",
                    description="Estimated energy consumption in kWh (e.g. '3.5')",
                    required=False,
                ),
            ],
            builder=build_plan_compute_workload_prompt,
        ),
        MCPPrompt(
            name="prepare_space_transmission",
            description="Calculate next satellite overpass and queue cryptographically signed RFC 9171 DTN bundles.",
            arguments=[
                MCPPromptArgument(
                    name="destination",
                    description="RFC 9171 Endpoint ID (e.g. 'dtn://ground-station-alpha.earth/telemetry')",
                    required=False,
                )
            ],
            builder=build_prepare_space_transmission_prompt,
        ),
        MCPPrompt(
            name="onboard_enterprise_workload",
            description="Step-by-step diagnostic and scaffolding prompt for onboarding third-party enterprise workloads.",
            arguments=[
                MCPPromptArgument(
                    name="project_description",
                    description="Natural language summary of the enterprise application to onboard",
                    required=True,
                )
            ],
            builder=build_onboard_enterprise_workload_prompt,
        ),
        MCPPrompt(
            name="optimize_uav_coverage",
            description="Workflow prompt to execute Scene-Aware PPO optimization and 5G network slicing adjustments.",
            arguments=[
                MCPPromptArgument(
                    name="target_receiver",
                    description="Disadvantaged user identifier to prioritize (default: 'Rx1')",
                    required=False,
                )
            ],
            builder=build_optimize_uav_coverage_prompt,
        ),
    ]


def build_optimize_uav_coverage_prompt(args: dict[str, str]) -> list[dict[str, Any]]:
    """Builds a guided prompt for autonomous UAV positioning optimization and 5G slicing."""
    target_rx = args.get("target_receiver", "Rx1")
    return [
        {
            "role": "user",
            "content": {
                "type": "text",
                "text": (
                    f"You are the Sovereign Metaverse Wireless Management Copilot.\n"
                    f"Operator requests positioning optimization prioritizing disadvantaged receiver '{target_rx}'.\n\n"
                    f"Please perform the following operational workflow:\n"
                    f"1. Read `smdc://metaverse/uav/status` and `smdc://metaverse/5g/slices` to inspect current link qualities and slice latencies.\n"
                    f"2. Execute `run_metaverse_sim_cycle(cycles=5)` to run Scene-Aware PPO forward passes with Sionna ray-tracing.\n"
                    f"3. Validate the new position against DePIN smart contract SLA rules via `validate_depin_sla` (ensuring SINR >= -15 dB).\n"
                    f"4. Summarize the capacity improvements and URLLC command latency (< 1ms)."
                ),
            },
        }
    ]


def build_onboard_enterprise_workload_prompt(args: dict[str, str]) -> list[dict[str, Any]]:
    """Builds a guided prompt for scaffolding and onboarding an enterprise application."""
    project_desc = args.get(
        "project_description", "An industrial IoT telemetry aggregator with real-time anomaly detection"
    )
    return [
        {
            "role": "user",
            "content": {
                "type": "text",
                "text": (
                    f"You are the Sovereign Mini Datacenter Enterprise Onboarding Copilot.\n"
                    f"The enterprise operator wants to onboard the following project: '{project_desc}'.\n\n"
                    f"Please perform the following onboarding workflow:\n"
                    f"1. Call `scaffold_enterprise_app` with appropriate operational category, runtime, and power shedding tier.\n"
                    f"2. Inspect the current node capacity by reading `smdc://system/manifest` and `get_telemetry`.\n"
                    f"3. Verify if GPU vRAM or persistent NVMe storage quotas fit within available hardware limits.\n"
                    f"4. Provide the operator with clear instructions on validating (`smdc app validate`), registering (`smdc app register`), and managing the application (`manage_enterprise_app`)."
                ),
            },
        }
    ]
