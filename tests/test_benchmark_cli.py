"""Unit tests for the smdc benchmark, demo, and mesh consensus CLI subcommands."""

import json
from unittest.mock import patch

from sovereign_dc.cli import cmd_benchmark, cmd_demo, cmd_mesh_consensus, main


class MockArgs:
    """Mock namespace for argument testing."""

    def __init__(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_cmd_benchmark_all(tmp_path) -> None:
    export_file = str(tmp_path / "bench_out.json")
    args = MockArgs(all=True, ai=False, dtn=False, system=False, model="qwen2.5-coder:7b", export=export_file)

    cmd_benchmark(args)

    # Verify JSON export
    with open(export_file, encoding="utf-8") as f:
        data = json.load(f)

    assert "benchmarks" in data
    assert "ai_embedding" in data["benchmarks"]
    assert "dtn_spool" in data["benchmarks"]
    assert "system_memory" in data["benchmarks"]
    assert data["benchmarks"]["ai_embedding"]["chunks_per_second"] > 0


def test_cmd_benchmark_individual_flags() -> None:
    # Test only AI
    args_ai = MockArgs(all=False, ai=True, dtn=False, system=False, model="llama3.2:3b", export=None)
    cmd_benchmark(args_ai)

    # Test only DTN
    args_dtn = MockArgs(all=False, ai=False, dtn=True, system=False, model=None, export=None)
    cmd_benchmark(args_dtn)

    # Test only System
    args_sys = MockArgs(all=False, ai=False, dtn=False, system=True, model=None, export=None)
    cmd_benchmark(args_sys)


def test_cmd_demo() -> None:
    args = MockArgs(steps=2, no_delay=True)
    cmd_demo(args)


def test_cmd_mesh_consensus() -> None:
    args = MockArgs(nodes=4)
    cmd_mesh_consensus(args)


def test_cli_dispatch_benchmark_and_demo() -> None:
    with patch("sys.argv", ["smdc", "benchmark", "--system"]):
        main()

    with patch("sys.argv", ["smdc", "demo", "--steps", "1", "--no-delay"]):
        main()

    with patch("sys.argv", ["smdc", "mesh", "consensus", "--nodes", "3"]):
        main()
