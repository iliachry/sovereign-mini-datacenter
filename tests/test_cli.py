import os
import subprocess
import sys

ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def test_smdc_help():
    res = subprocess.run(
        [sys.executable, "-m", "sovereign_dc.cli", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
    )
    assert res.returncode == 0
    assert "Sovereign Mini Datacenter" in res.stdout
    assert "status" in res.stdout
    assert "space" in res.stdout
    assert "mesh" in res.stdout


def test_smdc_version():
    res = subprocess.run(
        [sys.executable, "-m", "sovereign_dc.cli", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
    )
    assert res.returncode == 0
    assert "smdc" in res.stdout or "1." in res.stdout


def test_smdc_space_passes_cli():
    res = subprocess.run(
        [sys.executable, "-m", "sovereign_dc.cli", "space", "passes", "--hours", "6"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
    )
    assert res.returncode == 0
    assert "Upcoming Space Contact Passes" in res.stdout
    assert "Starlink" in res.stdout or "Swarm" in res.stdout


def test_smdc_mesh_cli():
    res = subprocess.run(
        [sys.executable, "-m", "sovereign_dc.cli", "mesh"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
    )
    assert res.returncode == 0
    assert "Sovereign Global Mesh" in res.stdout
    assert "smdc-node-01" in res.stdout


def test_smdc_docs_cli():
    res = subprocess.run(
        [sys.executable, "-m", "sovereign_dc.cli", "docs"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
    )
    assert res.returncode == 0
    assert "https://github.com/iliachry/sovereign-mini-datacenter" in res.stdout


def test_smdc_agent_cli():
    res = subprocess.run(
        [sys.executable, "-m", "sovereign_dc.cli", "agent", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
    )
    assert res.returncode == 0
    assert "status" in res.stdout
    assert "ask" in res.stdout
    assert "review" in res.stdout
    assert "index" in res.stdout


def test_smdc_mesh_chaos_cli():
    res = subprocess.run(
        [sys.executable, "-m", "sovereign_dc.cli", "mesh", "chaos", "--scenario", "split-brain"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
    )
    assert res.returncode == 0
    assert "Chaos Engineering Suite" in res.stdout
    assert "split_brain_partition" in res.stdout
    assert "PASSED" in res.stdout


def test_smdc_security_pqc_cli():
    res = subprocess.run(
        [sys.executable, "-m", "sovereign_dc.cli", "security", "pqc"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
    )
    assert res.returncode == 0
    assert "Post-Quantum Cryptography" in res.stdout
    assert "ML-DSA-65" in res.stdout
    assert "ML-KEM-768" in res.stdout
    assert "AUTHENTICATED" in res.stdout


def test_smdc_security_audit_cli():
    res = subprocess.run(
        [sys.executable, "-m", "sovereign_dc.cli", "security", "audit"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
    )
    assert res.returncode == 0
    assert "Security Compliance Audit" in res.stdout


def test_smdc_dashboard_cli_help():
    res = subprocess.run(
        [sys.executable, "-m", "sovereign_dc.cli", "dashboard", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
    )
    assert res.returncode == 0
    assert "--port" in res.stdout
    assert "--no-browser" in res.stdout
